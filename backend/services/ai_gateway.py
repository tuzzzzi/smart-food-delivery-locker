import json
import os
import subprocess
from pathlib import Path

from flask import current_app

from services.integration_runtime import get_ai_integration_config


RUNTIME_ERROR_STATUSES = {
    "snapshot_missing",
    "script_missing",
    "yolo_root_missing",
    "weights_missing",
    "dependency_missing",
    "script_failed",
    "runtime_error",
    "invoke_failed",
    "invalid_output",
}

RETRYABLE_STATUSES = {
    "snapshot_pending",
}


def _subprocess_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _latest_snapshot_path(task):
    artifact_root = current_app.config.get("ARTIFACTS_DIR", "")
    task_id = (getattr(task, "task_id", None) or "").strip()
    if not artifact_root or not task_id:
        return None, None, None

    locker_root = Path(artifact_root) / "locker"
    if not locker_root.exists():
        return None, None, None

    image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    latest_file = None
    latest_sort_key = None

    for deposit_dir in locker_root.glob(f"*/{task_id}/deposit"):
        if not deposit_dir.is_dir():
            continue
        for candidate in deposit_dir.iterdir():
            if not candidate.is_file() or candidate.suffix.lower() not in image_exts:
                continue
            stat = candidate.stat()
            sort_key = (stat.st_mtime, candidate.name)
            if latest_sort_key is None or sort_key > latest_sort_key:
                latest_sort_key = sort_key
                latest_file = candidate

    if latest_file is None:
        return None, None, None

    artifact_root_path = Path(artifact_root)
    relative_path = latest_file.relative_to(artifact_root_path).as_posix()
    absolute_path = str(latest_file)
    archive_directory = latest_file.parent.relative_to(artifact_root_path).as_posix()
    return relative_path, absolute_path, archive_directory


def _base_result(task, box, config, *, snapshot_path=None, archive_directory=None, weights_path=None):
    return {
        "ok": False,
        "mode": config.get("mode", "local"),
        "providerName": config.get("providerName", "local-yolov5"),
        "serviceUrl": config.get("serviceUrl", ""),
        "cameraStreamUrl": config.get("cameraStreamUrl", ""),
        "fallbackUsed": False,
        "taskId": getattr(task, "task_id", None),
        "boxNo": getattr(box, "box_no", None),
        "archiveDirectory": archive_directory,
        "snapshotPath": snapshot_path,
        "weightsPath": weights_path,
    }


def _parse_json_payload(stdout: str):
    if not stdout:
        return None

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            continue

    decoder = json.JSONDecoder()
    for index in range(len(stdout) - 1, -1, -1):
        if stdout[index] != "{":
            continue
        candidate = stdout[index:].strip()
        try:
            payload, _ = decoder.raw_decode(candidate)
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    return None


def _extract_confidence(detections):
    best = None
    for item in detections or []:
        raw = item.get("confidence")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if best is None or value > best:
            best = value
    return best


def _finalize_payload(payload, base_payload, *, stdout="", stderr=""):
    merged = dict(base_payload)
    if isinstance(payload, dict):
        merged.update(payload)

    merged.setdefault("mode", base_payload["mode"])
    merged.setdefault("providerName", base_payload["providerName"])
    merged.setdefault("serviceUrl", base_payload["serviceUrl"])
    merged.setdefault("cameraStreamUrl", base_payload["cameraStreamUrl"])
    merged.setdefault("fallbackUsed", False)
    merged.setdefault("taskId", base_payload["taskId"])
    merged.setdefault("boxNo", base_payload["boxNo"])
    merged.setdefault("archiveDirectory", base_payload["archiveDirectory"])
    merged.setdefault("snapshotPath", base_payload["snapshotPath"])
    merged.setdefault("weightsPath", base_payload["weightsPath"])
    if stdout:
        merged["stdout"] = stdout
    if stderr:
        merged["stderr"] = stderr

    confidence = _extract_confidence(merged.get("detections"))
    if confidence is not None:
        merged["confidence"] = confidence
    return merged


def _log_detection_start(task_id, box_no, snapshot_path, weights_path):
    current_app.logger.info(
        "ai.detect.start taskId=%s boxNo=%s snapshotPath=%s weightsPath=%s",
        task_id or "-",
        box_no or "-",
        snapshot_path or "-",
        weights_path or "-",
    )


def _log_detection_done(payload):
    detector_status = payload.get("detectorStatus") or "unknown"
    log_fn = current_app.logger.error if detector_status in RUNTIME_ERROR_STATUSES else current_app.logger.info
    log_fn(
        "ai.detect.done taskId=%s detectorStatus=%s ok=%s confidence=%s outputDir=%s",
        payload.get("taskId") or "-",
        detector_status,
        payload.get("ok"),
        payload.get("confidence"),
        payload.get("outputDir") or "-",
    )
    if detector_status in RUNTIME_ERROR_STATUSES:
        current_app.logger.error(
            "ai.detect.error taskId=%s detail=%s stderr=%s",
            payload.get("taskId") or "-",
            payload.get("detail") or "-",
            payload.get("stderr") or "-",
        )


def _run_local_detector(task, box, config):
    script_path = (current_app.config.get("AI_LOCAL_SCRIPT") or "").strip()
    yolo_root = (current_app.config.get("AI_YOLO_ROOT") or "").strip()
    weights_path = (current_app.config.get("AI_LOCAL_WEIGHTS") or "").strip()
    python_bin = (current_app.config.get("AI_PYTHON_BIN") or "python").strip()
    conf_thres = (current_app.config.get("AI_CONFIDENCE_THRESHOLD") or "0.25").strip()
    imgsz = str(current_app.config.get("AI_IMAGE_SIZE", 640))
    timeout_seconds = max(1, int(current_app.config.get("AI_TIMEOUT_MS", 15000) / 1000))
    snapshot_rel, snapshot_abs, archive_directory = _latest_snapshot_path(task)

    base_payload = _base_result(
        task,
        box,
        config,
        snapshot_path=snapshot_rel,
        archive_directory=archive_directory,
        weights_path=weights_path,
    )

    _log_detection_start(base_payload["taskId"], base_payload["boxNo"], snapshot_rel, weights_path)

    if not snapshot_abs or not os.path.exists(snapshot_abs):
        payload = _finalize_payload({
            "detectorStatus": "snapshot_pending",
            "retryable": True,
            "message": "入柜照片正在生成，请稍后",
            "detail": "入柜照片正在生成，请稍后",
        }, base_payload)
        _log_detection_done(payload)
        return payload

    if not script_path or not os.path.exists(script_path):
        payload = _finalize_payload({
            "detectorStatus": "script_missing",
            "detail": "未找到本地检测脚本 ai/inference/detect_snapshot.py。",
        }, base_payload)
        _log_detection_done(payload)
        return payload

    if not yolo_root or not os.path.exists(yolo_root):
        payload = _finalize_payload({
            "detectorStatus": "yolo_root_missing",
            "detail": "未找到 YOLOv5 运行目录 ai/yolov5。",
        }, base_payload)
        _log_detection_done(payload)
        return payload

    if not weights_path or not os.path.exists(weights_path):
        payload = _finalize_payload({
            "detectorStatus": "weights_missing",
            "detail": "未找到 YOLOv5 权重文件。",
        }, base_payload)
        _log_detection_done(payload)
        return payload

    command = [
        python_bin,
        script_path,
        "--image",
        snapshot_abs,
        "--weights",
        weights_path,
        "--yolo-root",
        yolo_root,
        "--conf-thres",
        conf_thres,
        "--imgsz",
        imgsz,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_subprocess_env(),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        payload = _finalize_payload({
            "detectorStatus": "runtime_error",
            "detail": f"本地 YOLOv5 检测超时，超过 {timeout_seconds} 秒。",
            "stdout": (exc.stdout or "").strip(),
            "stderr": (exc.stderr or "").strip(),
        }, base_payload)
        _log_detection_done(payload)
        return payload
    except Exception as exc:
        payload = _finalize_payload({
            "detectorStatus": "runtime_error",
            "detail": f"本地 YOLOv5 检测启动失败：{exc}",
        }, base_payload)
        _log_detection_done(payload)
        return payload

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    script_payload = _parse_json_payload(stdout)

    if result.returncode != 0:
        if isinstance(script_payload, dict):
            payload = _finalize_payload(script_payload, base_payload, stdout=stdout, stderr=stderr)
        else:
            payload = _finalize_payload({
                "detectorStatus": "runtime_error",
                "detail": "本地 YOLOv5 检测脚本返回了非 0 退出码。",
            }, base_payload, stdout=stdout, stderr=stderr)
        _log_detection_done(payload)
        return payload

    if not isinstance(script_payload, dict):
        payload = _finalize_payload({
            "detectorStatus": "invalid_output",
            "detail": "本地 YOLOv5 检测脚本未返回可解析的 JSON 结果。",
        }, base_payload, stdout=stdout, stderr=stderr)
        _log_detection_done(payload)
        return payload

    payload = _finalize_payload(script_payload, base_payload, stdout=stdout, stderr=stderr)
    _log_detection_done(payload)
    return payload


def request_box_detection(task, box):
    config = get_ai_integration_config()
    return _run_local_detector(task, box, config)


def handle_ai_callback(payload):
    config = get_ai_integration_config()
    return {
        "status": "success",
        "mode": config["mode"],
        "providerName": config["providerName"],
        "message": "AI 回调入口已预留，后续可在此接入外部视觉服务。",
        "payload": payload,
    }
