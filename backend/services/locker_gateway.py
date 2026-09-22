import json
import os
import random
import shutil
import time
from datetime import datetime, timedelta

from flask import current_app

from extensions import db
from models import Box, LockerCommand, LockerEvent, Package, Task
from services.audit_log import log_action
from services.integration_runtime import get_locker_integration_config
from services.package_pickup import finalize_package_pickup
from services.platform_flow import (
    emit_platform_event,
    mark_order_waiting_ai,
    mark_task_exception_pending,
    mark_task_exception_resolved,
)
from utils.time_utils import project_date_key


LOCKER_ACTIVE_STATUSES = {
    "created",
    "accepted",
    "relay_triggered",
    "waiting_lock_feedback",
    "waiting_door_open",
    "door_opened",
}

LOCKER_ALERT_STATUSES = {
    "failed",
    "open_timeout",
    "door_close_timeout",
    "sequence_error",
}

LOCKER_TERMINAL_STATUSES = LOCKER_ALERT_STATUSES | {"completed"}

LOCKER_LOCK_EVENT_TYPES = {
    "lock_opened",
    "lock_failed",
    "command_failed",
    "device_offline",
}

LOCKER_DOOR_EVENT_TYPES = {
    "door_opened",
    "door_closed",
    "door_close_timeout",
    "sequence_error",
}

LOCKER_ALERT_EVENT_TYPES = {
    "lock_failed",
    "command_failed",
    "device_offline",
    "open_timeout",
    "door_close_timeout",
    "sequence_error",
}

BUSINESS_PENDING = "pending"
BUSINESS_WAITING_CLOSE = "waiting_close"
BUSINESS_COMPLETED = "completed"
BUSINESS_EXCEPTION = "exception"

LOCKER_EXECUTABLE_STATUSES = {"created"}


def _command_scene_from_values(operator_type: str = "", reason: str = ""):
    raw_operator = (operator_type or "").strip().lower()
    raw_reason = (reason or "").strip().lower()

    if raw_reason in {"courier_put_in", "deposit", "store", "put_in_box", "delivery_put_in"}:
        return "deposit"
    if raw_reason in {"pickup", "pickup_code", "package_detail", "user_pickup"}:
        return "pickup"
    if raw_operator == "courier":
        return "deposit"
    if raw_operator == "user":
        return "pickup"
    return "manual"


def _command_scene_from_command(command: LockerCommand):
    if not command:
        return "manual"
    return _command_scene_from_values(command.operator_type, command.reason)


def _waiting_close_note(command: LockerCommand):
    scene = _command_scene_from_command(command)
    if scene == "deposit":
        return "Door opened. Waiting for courier to close the door before AI verification starts."
    if scene == "pickup":
        return "Door opened. Waiting for the door to close before finishing pickup."
    return "Door opened. Waiting for the door to close before finishing the locker loop."


def _closed_loop_detail(command: LockerCommand):
    scene = _command_scene_from_command(command)
    if scene == "deposit":
        return "Door closed callback completed locker deposit loop"
    if scene == "pickup":
        return "Door closed callback completed locker pickup loop"
    return "Door closed callback completed locker loop"


def _command_scene_label(scene: str):
    mapping = {
        "deposit": "配送员存放",
        "pickup": "用户取件",
        "manual": "手工联调",
    }
    return mapping.get((scene or "").strip().lower(), scene or "手工联调")


def _artifact_root():
    root = ""
    try:
        root = (current_app.config.get("ARTIFACTS_DIR") or "").strip()
    except Exception:
        root = ""

    if not root:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "artifacts"))
    os.makedirs(root, exist_ok=True)
    return root


def _command_trace_date(command: LockerCommand):
    anchor = (
        getattr(command, "created_at", None)
        or getattr(command, "recording_started_at", None)
        or _utcnow()
    )
    return project_date_key(anchor)


def _clean_identifier(value):
    raw = (value or "").strip()
    if not raw or raw.lower() in {"null", "none", "unknown"}:
        return None
    return raw


def _command_trace_task_id(command: LockerCommand):
    if not command:
        return "TASK-UNASSIGNED"

    task_id = _clean_identifier(getattr(command, "task_id", None))
    if task_id:
        return task_id

    package_id = _clean_identifier(getattr(command, "package_id", None))
    if package_id:
        pkg = Package.query.get(package_id)
        pkg_task_id = _clean_identifier(getattr(pkg, "task_id", None)) if pkg else None
        if pkg_task_id:
            command.task_id = pkg_task_id
            return pkg_task_id

    box_no = _clean_identifier(getattr(command, "box_no", None))
    if box_no:
        box = Box.query.get(box_no)
        box_task_id = _clean_identifier(getattr(box, "task_id", None)) if box else None
        if box_task_id:
            command.task_id = box_task_id
            return box_task_id

    return "TASK-UNASSIGNED"


def _command_trace_catalog(command: LockerCommand, scene: str = None):
    scene_key = (scene or _command_scene_from_command(command) or "manual").strip().lower() or "manual"
    command_id = getattr(command, "command_id", "") or ""

    if scene_key == "deposit":
        return {
            "scene": "deposit",
            "sceneLabel": "配送员存放",
            "snapshot": {
                "filename": f"courier-close-{command_id}.jpg",
                "label": "配送员关柜图",
            },
            "result_image": {
                "filename": f"courier-result-{command_id}.jpg",
                "label": "入柜结果图",
            },
            "video": {
                "filename": f"courier-trace-{command_id}.mp4",
                "label": "配送员存放视频",
            },
            "previewFallbackLabel": "配送员关柜图",
            "openImageLabel": "配送员开柜图",
        }

    if scene_key == "pickup":
        return {
            "scene": "pickup",
            "sceneLabel": "用户取件",
            "snapshot": {
                "filename": f"user-close-{command_id}.jpg",
                "label": "用户关柜图",
            },
            "result_image": {
                "filename": f"user-open-{command_id}.jpg",
                "label": "用户开柜图",
            },
            "video": {
                "filename": f"user-trace-{command_id}.mp4",
                "label": "用户取件视频",
            },
            "previewFallbackLabel": "用户关柜图",
            "openImageLabel": "用户开柜图",
        }

    return {
        "scene": "manual",
        "sceneLabel": "手工联调",
        "snapshot": {
            "filename": f"manual-close-{command_id}.jpg",
            "label": "关柜图",
        },
        "result_image": {
            "filename": f"manual-result-{command_id}.jpg",
            "label": "结果图",
        },
        "video": {
            "filename": f"manual-trace-{command_id}.mp4",
            "label": "留痕视频",
        },
        "previewFallbackLabel": "关柜图",
        "openImageLabel": "开柜图",
    }


def _command_trace_directory(command: LockerCommand, scene: str = None):
    scene_key = (scene or _command_scene_from_command(command) or "manual").strip().lower() or "manual"
    task_id = _command_trace_task_id(command)
    return f"locker/{_command_trace_date(command)}/{task_id}/{scene_key}"


def build_command_artifact_relative_path(command: LockerCommand, *, kind: str, scene: str = None):
    if not command:
        return None

    kind_key = (kind or "").strip().lower()
    if kind_key == "meta":
        filename = "meta.json"
    else:
        filename = (_command_trace_catalog(command, scene=scene).get(kind_key) or {}).get("filename")
    if not filename:
        raise ValueError(f"Unsupported locker artifact kind: {kind}")
    return f"{_command_trace_directory(command, scene=scene)}/{filename}"


def _command_artifact_abs_path(relative_path: str):
    if not relative_path:
        return None
    clean = relative_path.replace("\\", "/").lstrip("/")
    return os.path.join(_artifact_root(), *clean.split("/"))


def _normalize_artifact_relative_path(path: str):
    raw = (path or "").strip()
    if not raw:
        return None

    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    clean = raw.replace("\\", "/").lstrip("/")
    if clean.startswith("artifacts/"):
        clean = clean[len("artifacts/"):]
    return clean or None


def _command_artifact_exists(relative_path: str):
    normalized = _normalize_artifact_relative_path(relative_path)
    if not normalized or normalized.startswith("http://") or normalized.startswith("https://"):
        return False
    absolute_path = _command_artifact_abs_path(normalized)
    return bool(absolute_path and os.path.exists(absolute_path))


def _command_existing_artifact_paths(command: LockerCommand):
    if not command:
        return []

    candidates = [
        command.snapshot_path,
        command.result_image_path,
        command.video_path,
    ]
    result = []
    seen = set()
    for candidate in candidates:
        clean = _normalize_artifact_relative_path(candidate)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if _command_artifact_exists(clean):
            result.append(clean)
    return result


def _prune_empty_trace_dirs(relative_path: str):
    absolute_path = _command_artifact_abs_path(relative_path)
    if not absolute_path:
        return

    locker_root = os.path.join(_artifact_root(), "locker")
    current = os.path.dirname(absolute_path)

    while current and os.path.abspath(current).startswith(os.path.abspath(locker_root)):
        if not os.path.isdir(current):
            current = os.path.dirname(current)
            continue
        if os.path.abspath(current) == os.path.abspath(locker_root):
            break
        if os.listdir(current):
            break
        os.rmdir(current)
        current = os.path.dirname(current)


def _copy_command_artifact(source_relative_path: str, target_relative_path: str):
    source = _normalize_artifact_relative_path(source_relative_path)
    target = _normalize_artifact_relative_path(target_relative_path)
    if not source or not target:
        return False

    source_abs = _command_artifact_abs_path(source)
    target_abs = _command_artifact_abs_path(target)
    if not source_abs or not target_abs or not os.path.exists(source_abs):
        return False

    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    if os.path.abspath(source_abs) != os.path.abspath(target_abs) and not os.path.exists(target_abs):
        shutil.copy2(source_abs, target_abs)
    return os.path.exists(target_abs)


def _legacy_command_artifact_candidates(command: LockerCommand, kind: str, scene: str):
    date_key = _command_trace_date(command)
    scene_key = (scene or _command_scene_from_command(command) or "manual").strip().lower() or "manual"
    legacy_dir = f"locker/{scene_key}/{command.command_id}"
    dated_legacy_dir = f"locker/{date_key}/{scene_key}/{command.command_id}"

    candidates = []
    if kind == "snapshot":
        candidates.extend([
            command.snapshot_path,
            build_command_artifact_relative_path(command, kind="snapshot", scene=scene_key),
            f"{dated_legacy_dir}/close-frame.jpg",
            f"{legacy_dir}/close-frame.jpg",
        ])
        if command.task_id:
            candidates.append(f"tasks/{command.task_id}/{date_key}/{command.command_id}-close-frame.jpg")
        if command.package_id:
            candidates.append(f"packages/{command.package_id}/{date_key}/{command.command_id}-close-frame.jpg")
    elif kind == "result_image":
        candidates.extend([
            command.result_image_path,
            build_command_artifact_relative_path(command, kind="result_image", scene=scene_key),
            f"{dated_legacy_dir}/result.jpg",
            f"{dated_legacy_dir}/ai-result.jpg",
            f"{legacy_dir}/result.jpg",
            f"{legacy_dir}/ai-result.jpg",
        ])
        if not _command_ai_participates(scene_key) and command.package_id:
            candidates.append(f"packages/{command.package_id}/{date_key}/{command.command_id}-close-frame.jpg")
    elif kind == "video":
        candidates.extend([
            command.video_path,
            build_command_artifact_relative_path(command, kind="video", scene=scene_key),
            f"{dated_legacy_dir}/trace.mp4",
            f"{legacy_dir}/trace.mp4",
        ])

    normalized = []
    seen = set()
    for candidate in candidates:
        clean = _normalize_artifact_relative_path(candidate)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _normalize_command_trace_artifacts(command: LockerCommand):
    if not command:
        return

    scene = (command.recording_scene or _command_scene_from_command(command) or "manual").strip().lower() or "manual"
    command.recording_scene = scene
    resolved_task_id = _command_trace_task_id(command)
    if resolved_task_id != "TASK-UNASSIGNED":
        command.task_id = resolved_task_id

    snapshot_target = build_command_artifact_relative_path(command, kind="snapshot", scene=scene)
    snapshot_found = False
    for candidate in _legacy_command_artifact_candidates(command, "snapshot", scene):
        if _copy_command_artifact(candidate, snapshot_target):
            snapshot_found = True
            break
    command.snapshot_path = snapshot_target
    if snapshot_found:
        if command.snapshot_status in {None, "", "pending", "unavailable"}:
            command.snapshot_status = "captured"
    elif command.snapshot_status == "captured":
        command.snapshot_status = "unavailable"

    video_target = build_command_artifact_relative_path(command, kind="video", scene=scene)
    video_found = False
    for candidate in _legacy_command_artifact_candidates(command, "video", scene):
        if _copy_command_artifact(candidate, video_target):
            video_found = True
            break
    command.video_path = video_target
    if video_found:
        if command.video_status in {None, "", "pending"}:
            command.video_status = "stored"
    elif command.video_status == "stored":
        command.video_status = "pending"

    if _command_ai_participates(scene):
        result_target = build_command_artifact_relative_path(command, kind="result_image", scene=scene)
        result_found = False
        for candidate in _legacy_command_artifact_candidates(command, "result_image", scene):
            if _copy_command_artifact(candidate, result_target):
                result_found = True
                break
        command.result_image_path = result_target
        if result_found:
            if command.result_image_status in {None, "", "pending", "waiting_ai", "unavailable"}:
                command.result_image_status = "ready"
        elif command.result_image_status == "ready":
            command.result_image_status = "unavailable"
    else:
        command.result_image_path = build_command_artifact_relative_path(command, kind="result_image", scene=scene)
        if not command.result_image_status:
            command.result_image_status = "not_required"


def _command_trace_labels(command: LockerCommand, scene: str = None):
    catalog = _command_trace_catalog(command, scene=scene)
    return {
        "sceneLabel": catalog.get("sceneLabel"),
        "snapshotLabel": (catalog.get("snapshot") or {}).get("label"),
        "resultImageLabel": (catalog.get("result_image") or {}).get("label"),
        "videoLabel": (catalog.get("video") or {}).get("label"),
        "openImageLabel": catalog.get("openImageLabel"),
        "previewFallbackLabel": catalog.get("previewFallbackLabel"),
    }


def _command_ai_participates(scene: str) -> bool:
    return (scene or "").strip().lower() == "deposit"


def _ensure_command_trace_defaults(command: LockerCommand):
    if not command:
        return

    scene = _command_scene_from_command(command)

    if not command.recording_scene:
        command.recording_scene = scene

    command.ai_participates = _command_ai_participates(scene)

    if not command.recording_status:
        command.recording_status = "idle"
    if not command.video_status:
        command.video_status = "pending"
    if not command.video_path:
        command.video_path = build_command_artifact_relative_path(command, kind="video", scene=scene)
    if not command.snapshot_status:
        command.snapshot_status = "pending"
    if not command.snapshot_path:
        command.snapshot_path = build_command_artifact_relative_path(command, kind="snapshot", scene=scene)

    if command.ai_participates:
        if not command.result_image_status:
            command.result_image_status = "waiting_ai"
        if not command.result_image_path:
            command.result_image_path = build_command_artifact_relative_path(command, kind="result_image", scene=scene)
    else:
        if not command.result_image_status:
            command.result_image_status = "not_required"

    _normalize_command_trace_artifacts(command)


def _mark_command_recording_started(command: LockerCommand, now=None):
    if not command:
        return

    now = now or _utcnow()
    _ensure_command_trace_defaults(command)

    if not command.recording_started_at:
        command.recording_started_at = now
    command.recording_status = "recording"
    command.video_status = "recording"


def _mark_command_recording_stopped(command: LockerCommand, now=None):
    if not command:
        return

    now = now or _utcnow()
    _ensure_command_trace_defaults(command)

    if not command.recording_started_at:
        command.recording_started_at = now
    if not command.recording_stopped_at:
        command.recording_stopped_at = now
    command.recording_status = "stopped"
    command.video_status = "stored"
    command.snapshot_status = "captured"


def _mark_command_trace_exception(command: LockerCommand, now=None):
    if not command:
        return

    now = now or _utcnow()
    _ensure_command_trace_defaults(command)

    if command.recording_started_at and not command.recording_stopped_at:
        command.recording_stopped_at = now
    if command.recording_status == "recording":
        command.recording_status = "interrupted"
    elif command.recording_status == "idle":
        command.recording_status = "idle"
    command.video_status = "exception"


def _locker_trace_status_label(kind: str, status: str) -> str:
    value = (status or "").strip().lower()
    if kind == "recording":
        mapping = {
            "idle": "待触发",
            "recording": "录像中",
            "stopped": "录像已停止",
            "interrupted": "录像异常中断",
        }
    elif kind == "video":
        mapping = {
            "pending": "待生成留痕",
            "recording": "录像中",
            "stored": "留痕已记录",
            "exception": "留痕异常",
        }
    elif kind == "snapshot":
        mapping = {
            "pending": "待生成快照",
            "captured": "快照已生成",
            "unavailable": "暂无快照",
        }
    else:
        mapping = {
            "waiting_ai": "等待确认结果图",
            "ready": "结果图已生成",
            "ai_failed": "确认未通过结果图",
            "not_required": "本场景无需结果图",
            "unavailable": "暂无结果图",
        }
    return mapping.get(value, status or "-")


def _is_public_preview_path(path: str) -> bool:
    raw = (path or "").strip().lower()
    return raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/")


def _artifact_public_url(path: str):
    raw = (path or "").strip()
    if not raw:
        return None

    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    if raw.startswith("/"):
        return raw

    prefix = "/artifacts"
    try:
        prefix = current_app.config.get("ARTIFACTS_URL_PREFIX", "/artifacts") or "/artifacts"
    except Exception:
        prefix = "/artifacts"
    clean = _normalize_artifact_relative_path(raw)
    if not clean or clean.startswith("http://") or clean.startswith("https://"):
        return raw
    return f"{prefix.rstrip('/')}/{clean}"


def _trace_meta_payload(command: LockerCommand):
    scene = _command_scene_from_command(command)
    labels = _command_trace_labels(command, scene=scene)
    archive_task_id = _command_trace_task_id(command)
    package_id = _clean_identifier(command.package_id)
    operator_type = _clean_identifier(command.operator_type)
    return {
        "commandId": command.command_id,
        "boxNo": command.box_no,
        "taskId": archive_task_id,
        "packageId": package_id,
        "operatorType": operator_type,
        "scene": scene,
        "sceneLabel": labels.get("sceneLabel"),
        "recordingScene": command.recording_scene,
        "aiParticipates": bool(command.ai_participates),
        "recordingStatus": command.recording_status,
        "videoStatus": command.video_status,
        "snapshotStatus": command.snapshot_status,
        "resultImageStatus": command.result_image_status,
        "archiveDirectory": _command_trace_directory(command, scene=command.recording_scene or scene),
        "snapshotLabel": labels.get("snapshotLabel"),
        "resultImageLabel": labels.get("resultImageLabel"),
        "videoLabel": labels.get("videoLabel"),
        "snapshotPath": command.snapshot_path,
        "resultImagePath": command.result_image_path,
        "videoPath": command.video_path,
        "snapshotUrl": _artifact_public_url(command.snapshot_path) if _command_artifact_exists(command.snapshot_path) else None,
        "resultImageUrl": _artifact_public_url(command.result_image_path) if _command_artifact_exists(command.result_image_path) else None,
        "videoUrl": _artifact_public_url(command.video_path) if _command_artifact_exists(command.video_path) else None,
        "recordingStartedAt": command.recording_started_at.isoformat() if command.recording_started_at else None,
        "recordingStoppedAt": command.recording_stopped_at.isoformat() if command.recording_stopped_at else None,
        "createdAt": command.created_at.isoformat() if command.created_at else None,
        "updatedAt": command.updated_at.isoformat() if command.updated_at else None,
    }


def sync_command_trace_meta(command: LockerCommand):
    if not command:
        return None

    _ensure_command_trace_defaults(command)
    relative_path = build_command_artifact_relative_path(command, kind="meta", scene=command.recording_scene or _command_scene_from_command(command))
    absolute_path = _command_artifact_abs_path(relative_path)
    if not _command_existing_artifact_paths(command):
        if absolute_path and os.path.exists(absolute_path):
            os.remove(absolute_path)
        _prune_empty_trace_dirs(relative_path)
        return None
    os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
    with open(absolute_path, "w", encoding="utf-8") as fp:
        json.dump(_trace_meta_payload(command), fp, ensure_ascii=False, indent=2, sort_keys=True)
    return relative_path


def serialize_locker_trace(command: LockerCommand, *, audience="admin"):
    if not command:
        return None

    payload = serialize_locker_command(command)
    scene = payload.get("commandScene") or payload.get("recordingScene") or "manual"
    labels = _command_trace_labels(command, scene=scene)
    placeholder_only = not any(
        _command_artifact_exists(payload.get(key))
        for key in ("snapshotPath", "resultImagePath", "videoPath")
    )

    trace = {
        "commandId": payload.get("commandId"),
        "boxNo": payload.get("boxNo"),
        "taskId": payload.get("taskId") or _command_trace_task_id(command),
        "packageId": payload.get("packageId"),
        "commandScene": scene,
        "commandSceneLabel": payload.get("commandSceneLabel") or labels.get("sceneLabel") or _command_scene_label(scene),
        "archiveDirectory": payload.get("archiveDirectory"),
        "snapshotLabel": payload.get("snapshotLabel") or labels.get("snapshotLabel"),
        "resultImageLabel": payload.get("resultImageLabel") or labels.get("resultImageLabel"),
        "videoLabel": payload.get("videoLabel") or labels.get("videoLabel"),
        "openImageLabel": payload.get("openImageLabel") or labels.get("openImageLabel"),
        "aiParticipates": bool(payload.get("aiParticipates")),
        "recordingStatus": payload.get("recordingStatus") or "idle",
        "recordingStatusLabel": _locker_trace_status_label("recording", payload.get("recordingStatus") or "idle"),
        "recordingStartedAt": payload.get("recordingStartedAt"),
        "recordingStoppedAt": payload.get("recordingStoppedAt"),
        "videoStatus": payload.get("videoStatus") or "pending",
        "videoStatusLabel": _locker_trace_status_label("video", payload.get("videoStatus") or "pending"),
        "snapshotStatus": payload.get("snapshotStatus") or "pending",
        "snapshotStatusLabel": _locker_trace_status_label("snapshot", payload.get("snapshotStatus") or "pending"),
        "resultImageStatus": payload.get("resultImageStatus") or ("waiting_ai" if payload.get("aiParticipates") else "not_required"),
        "resultImageStatusLabel": _locker_trace_status_label("result_image", payload.get("resultImageStatus") or ("waiting_ai" if payload.get("aiParticipates") else "not_required")),
        "placeholderOnly": placeholder_only,
        "placeholderNote": "当前为录像/图片留痕状态与路径占位，后续可接真实文件上传与预览。",
    }

    if audience == "admin":
        trace["videoPath"] = payload.get("videoPath")
        trace["snapshotPath"] = payload.get("snapshotPath")
        trace["resultImagePath"] = payload.get("resultImagePath")
    else:
        trace["snapshotPath"] = payload.get("snapshotPath") if _is_public_preview_path(payload.get("snapshotPath")) else None
        trace["resultImagePath"] = payload.get("resultImagePath") if _is_public_preview_path(payload.get("resultImagePath")) else None

    snapshot_url = (
        _artifact_public_url(payload.get("snapshotPath"))
        if payload.get("snapshotStatus") == "captured" and _command_artifact_exists(payload.get("snapshotPath"))
        else None
    )
    result_url = (
        _artifact_public_url(payload.get("resultImagePath"))
        if payload.get("resultImageStatus") == "ready" and _command_artifact_exists(payload.get("resultImagePath"))
        else None
    )
    trace["snapshotUrl"] = snapshot_url
    trace["resultImageUrl"] = result_url
    trace["videoUrl"] = (
        _artifact_public_url(payload.get("videoPath"))
        if audience == "admin" and _command_artifact_exists(payload.get("videoPath"))
        else None
    )
    trace["previewImageUrl"] = trace.get("resultImageUrl") or trace.get("snapshotUrl")
    trace["previewImageLabel"] = trace.get("resultImageLabel") if trace.get("resultImageUrl") else trace.get("snapshotLabel") or labels.get("previewFallbackLabel")

    return trace


def _utcnow():
    return datetime.utcnow()


def _gen_command_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def _trim_text(value, limit=255):
    text = (value or "").strip()
    return text[:limit] if text else None


def _dump_json(payload):
    if payload in (None, "", {}):
        return None
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps({"raw": str(payload)}, ensure_ascii=False, sort_keys=True)


def _normalize_signal(value):
    raw = (value or "").strip().lower()
    if raw in {"", "unknown", "none", "null"}:
        return None

    mapping = {
        "open": "open",
        "opened": "open",
        "door_open": "open",
        "closed": "closed",
        "close": "closed",
        "door_closed": "closed",
        "unlocked": "unlocked",
        "unlock": "unlocked",
        "opened_lock": "unlocked",
        "locked": "locked",
        "lock": "locked",
        "failed": "failed",
        "error": "failed",
        "low": "low",
        "ok": "ok",
        "online": "online",
        "offline": "offline",
    }
    return mapping.get(raw, raw)


def _normalize_event_type(payload):
    raw = (
        payload.get("eventType")
        or payload.get("event_type")
        or payload.get("event")
        or payload.get("status")
        or payload.get("signal")
        or ""
    ).strip().lower()

    mapping = {
        "accepted": "command_ack",
        "command_ack": "command_ack",
        "controller_ack": "command_ack",
        "queued": "command_ack",
        "relay_on": "relay_triggered",
        "relay_pulse": "relay_triggered",
        "relay_triggered": "relay_triggered",
        "unlock": "lock_opened",
        "unlocked": "lock_opened",
        "lock_feedback_opened": "lock_opened",
        "lock_opened": "lock_opened",
        "lock_feedback_failed": "lock_failed",
        "unlock_failed": "lock_failed",
        "lock_failed": "lock_failed",
        "open_failed": "lock_failed",
        "door_open": "door_opened",
        "door_opened": "door_opened",
        "magnet_open": "door_opened",
        "door_close": "door_closed",
        "door_closed": "door_closed",
        "magnet_closed": "door_closed",
        "timeout": "open_timeout",
        "open_timeout": "open_timeout",
        "close_timeout": "door_close_timeout",
        "door_close_timeout": "door_close_timeout",
        "door_not_closed": "door_close_timeout",
        "controller_error": "command_failed",
        "failed": "command_failed",
        "offline": "device_offline",
        "controller_offline": "device_offline",
        "power_offline": "device_offline",
        "heartbeat": "heartbeat",
    }
    if raw in mapping:
        return mapping[raw]

    controller_power_state = _normalize_signal(
        payload.get("controllerPowerState") or payload.get("controller_power_state")
    )
    lock_power_state = _normalize_signal(
        payload.get("lockPowerState") or payload.get("lock_power_state")
    )
    if controller_power_state == "offline" or lock_power_state == "offline":
        return "device_offline"

    door_state = _normalize_signal(payload.get("doorState") or payload.get("door_state"))
    if door_state == "open":
        return "door_opened"
    if door_state == "closed":
        return "door_closed"

    lock_state = _normalize_signal(payload.get("lockFeedback") or payload.get("lock_feedback"))
    if lock_state == "unlocked":
        return "lock_opened"
    if lock_state == "failed":
        return "lock_failed"

    return raw or "callback_received"


def _event_log_meta(event_type: str):
    mapping = {
        "command_ack": ("locker.command_ack", "Locker command acknowledged"),
        "lock_opened": ("locker.lock_feedback", "Lock feedback confirmed unlock"),
        "door_opened": ("locker.door_sensor", "Door sensor confirmed opened"),
        "door_closed": ("locker.door_sensor", "Door sensor confirmed closed"),
        "open_timeout": ("locker.command_timeout", "Locker open feedback timed out"),
        "door_close_timeout": ("locker.door_close_timeout", "Door was not closed in time"),
        "lock_failed": ("locker.command_failed", "Lock feedback reported failure"),
        "command_failed": ("locker.command_failed", "Locker command failed"),
        "device_offline": ("locker.command_failed", "Locker hardware reported offline"),
        "sequence_error": ("locker.sequence_error", "Door callback sequence is invalid"),
    }
    return mapping.get(event_type)


def _apply_command_update(
    command: LockerCommand,
    *,
    status: str = None,
    door_state: str = None,
    lock_feedback_state: str = None,
    controller_power_state: str = None,
    lock_power_state: str = None,
    detail: str = None,
    last_error: str = None,
    callback_payload=None,
    accepted: bool = None,
    pending_hardware: bool = None,
):
    now = _utcnow()
    _ensure_command_trace_defaults(command)

    if status:
        command.status = status
    if door_state:
        command.door_state = door_state
    if lock_feedback_state:
        command.lock_feedback_state = lock_feedback_state
    if controller_power_state:
        command.controller_power_state = controller_power_state
    if lock_power_state:
        command.lock_power_state = lock_power_state
    if detail is not None:
        command.detail = _trim_text(detail)
    if last_error is not None:
        command.last_error = _trim_text(last_error)
    if accepted is not None:
        command.accepted = bool(accepted)
        if accepted and not command.accepted_at:
            command.accepted_at = now
    if pending_hardware is not None:
        command.pending_hardware = bool(pending_hardware)
    if callback_payload is not None:
        command.last_callback_payload = _dump_json(callback_payload)

    if status == "relay_triggered":
        command.relay_triggered_at = command.relay_triggered_at or now
    if status == "waiting_door_open":
        command.lock_opened_at = command.lock_opened_at or now
        config = get_locker_integration_config()
        command.timeout_at = now + timedelta(
            milliseconds=int(config.get("openTimeoutMs") or 15000)
        )
    if status == "door_opened":
        command.door_opened_at = command.door_opened_at or now
        command.timeout_at = None
        if not command.door_close_timeout_at:
            config = get_locker_integration_config()
            command.door_close_timeout_at = now + timedelta(
                milliseconds=int(config.get("doorCloseTimeoutMs") or 30000)
            )
        _mark_command_recording_started(command, now=now)
    if status == "completed":
        command.door_closed_at = command.door_closed_at or now
        command.finished_at = command.finished_at or now
        command.pending_hardware = False
        command.timeout_at = None
        _mark_command_recording_stopped(command, now=now)
    if status in LOCKER_ALERT_STATUSES:
        command.finished_at = command.finished_at or now
        command.pending_hardware = False
        _mark_command_trace_exception(command, now=now)


def _set_business_state(command: LockerCommand, *, status: str = None, note: str = None):
    if command.business_status == BUSINESS_COMPLETED and status != BUSINESS_COMPLETED:
        return False

    if status:
        command.business_status = status
        if status == BUSINESS_COMPLETED:
            command.business_completed_at = command.business_completed_at or _utcnow()
    if note is not None:
        command.business_note = _trim_text(note)
    return True


def _record_locker_event(
    *,
    command: LockerCommand = None,
    box_no: str,
    event_type: str,
    source: str,
    detail: str = None,
    raw_payload=None,
):
    event = LockerEvent(
        command_id=getattr(command, "command_id", None),
        box_no=box_no,
        event_type=event_type,
        source=source,
        command_status=getattr(command, "status", None),
        door_state=getattr(command, "door_state", None),
        lock_feedback_state=getattr(command, "lock_feedback_state", None),
        controller_power_state=getattr(command, "controller_power_state", None),
        lock_power_state=getattr(command, "lock_power_state", None),
        detail=_trim_text(detail),
        raw_payload=_dump_json(raw_payload),
    )
    db.session.add(event)
    return event


def _resolve_command(command_id: str = "", box_no: str = ""):
    if command_id:
        return LockerCommand.query.get(command_id)
    if not box_no:
        return None
    return (
        LockerCommand.query
        .filter_by(box_no=box_no)
        .order_by(LockerCommand.created_at.desc())
        .first()
    )


def _resolve_latest_package_command(package_id: str):
    if not package_id:
        return None
    return (
        LockerCommand.query
        .filter_by(package_id=package_id)
        .order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )


def _resolve_latest_task_command(task_id: str):
    if not task_id:
        return None
    return (
        LockerCommand.query
        .filter_by(task_id=task_id)
        .order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )


def get_latest_task_locker_command(task_id: str, *, scene: str = None):
    if not task_id:
        return None

    query = LockerCommand.query.filter_by(task_id=task_id)
    if scene:
        query = query.filter_by(recording_scene=scene)
    command = (
        query.order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )
    if command:
        _ensure_command_trace_defaults(command)
    return command


def _resolve_latest_active_box_command(box_no: str):
    if not box_no:
        return None
    return (
        LockerCommand.query
        .filter_by(box_no=box_no, pending_hardware=True)
        .filter(LockerCommand.finished_at.is_(None))
        .order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )


def _resolve_latest_executable_box_command(box_no: str):
    if not box_no:
        return None
    return (
        LockerCommand.query
        .filter_by(box_no=box_no, pending_hardware=True)
        .filter(LockerCommand.finished_at.is_(None))
        .filter(LockerCommand.relay_triggered_at.is_(None))
        .filter(LockerCommand.lock_opened_at.is_(None))
        .filter(LockerCommand.door_opened_at.is_(None))
        .filter(LockerCommand.door_closed_at.is_(None))
        .filter(LockerCommand.status.in_(list(LOCKER_EXECUTABLE_STATUSES)))
        .order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )


def get_latest_package_locker_command(package_id: str, *, scene: str = None):
    if not package_id:
        return None

    query = LockerCommand.query.filter_by(package_id=package_id)
    if scene:
        query = query.filter_by(recording_scene=scene)
    command = (
        query.order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )
    if command:
        _ensure_command_trace_defaults(command)
    return command


def bind_package_to_latest_deposit_command(task_id: str, package_id: str):
    command = get_latest_task_locker_command(task_id, scene="deposit")
    if not command:
        return None

    _ensure_command_trace_defaults(command)
    if package_id and command.package_id != package_id:
        command.package_id = package_id
    return command


def mark_deposit_ai_result(task_id: str, *, passed: bool, detail: str = None, package_id: str = None):
    command = get_latest_task_locker_command(task_id, scene="deposit")
    if not command:
        return None

    _ensure_command_trace_defaults(command)
    if package_id and command.package_id != package_id:
        command.package_id = package_id
    command.result_image_status = "ready" if passed else "ai_failed"
    if detail:
        command.detail = _trim_text(detail)
    return command


def _matches_command_context(command: LockerCommand, *, scene: str, task_id: str = None, package_id: str = None):
    if not command:
        return False

    command_scene = _command_scene_from_command(command)
    if scene != "manual" and command_scene != scene:
        return False

    if scene == "pickup":
        return bool(package_id and command.package_id == package_id)
    if scene == "deposit":
        return bool(task_id and command.task_id == task_id)

    if package_id and command.package_id == package_id:
        return True
    if task_id and command.task_id == task_id:
        return True
    return False


def _command_has_event(command: LockerCommand, event_type: str):
    if not command:
        return False

    if event_type == "command_ack":
        return bool(command.accepted_at or command.accepted)
    if event_type == "relay_triggered":
        return bool(command.relay_triggered_at)
    if event_type == "lock_opened":
        return bool(command.lock_opened_at)
    if event_type == "door_opened":
        return bool(command.door_opened_at)
    if event_type == "door_closed":
        return bool(command.door_closed_at)
    if event_type in LOCKER_TERMINAL_STATUSES:
        return command.status == event_type
    return False


def _command_open_succeeded(command: LockerCommand):
    return bool(command and (command.lock_opened_at or command.door_opened_at))


def _command_closed_loop_completed(command: LockerCommand):
    return bool(command and command.door_opened_at and command.door_closed_at)


def _log_business(action: str, command: LockerCommand, detail: str, actor_type: str = "system", actor_openid: str = None):
    log_action(
        action,
        actor_type=actor_type,
        actor_openid=actor_openid or command.box_no,
        task_id=command.task_id,
        package_id=command.package_id,
        detail=detail,
    )


def _sync_task_exception_state_from_command(command: LockerCommand, *, resolved: bool, note: str = ""):
    if not command or _command_scene_from_command(command) != "deposit" or not command.task_id:
        return None

    task = Task.query.get(command.task_id)
    if not task:
        return None

    if resolved:
        mark_task_exception_resolved(task, note or "异常已处理完成，任务已恢复正常投递流程")
    else:
        mark_task_exception_pending(task, note or "当前入柜流程进入异常，等待配送员处理")
    return task


def _mark_business_exception(command: LockerCommand, detail: str, actor_type: str = "system", actor_openid: str = None):
    if _set_business_state(command, status=BUSINESS_EXCEPTION, note=detail):
        _sync_task_exception_state_from_command(command, resolved=False, note=detail)
        _log_business("locker.business_exception", command, detail, actor_type=actor_type, actor_openid=actor_openid)


def _mark_business_completed(command: LockerCommand, detail: str, actor_type: str = "system", actor_openid: str = None):
    if _set_business_state(command, status=BUSINESS_COMPLETED, note=detail):
        _log_business("locker.business_completed", command, detail, actor_type=actor_type, actor_openid=actor_openid)


def _finalize_pickup_from_command(command: LockerCommand, actor_type: str = "hardware"):
    detail = command.detail or _closed_loop_detail(command)

    if not _command_closed_loop_completed(command):
        return None

    if not command.package_id:
        _mark_business_completed(command, detail, actor_type=actor_type)
        return None

    pkg = Package.query.get(command.package_id)
    if not pkg:
        _mark_business_exception(
            command,
            "Locker loop closed but linked package was not found",
            actor_type=actor_type,
        )
        return None

    finalize_package_pickup(
        pkg,
        actor_type=actor_type if actor_type in {"hardware", "system", "user", "admin"} else "hardware",
        actor_openid=command.box_no,
        detail=detail,
    )
    _mark_business_completed(command, detail, actor_type=actor_type)
    return pkg


def _finalize_deposit_from_command(command: LockerCommand, actor_type: str = "hardware"):
    detail = command.detail or _closed_loop_detail(command)

    if not _command_closed_loop_completed(command):
        return None

    if not command.task_id:
        _mark_business_completed(command, detail, actor_type=actor_type)
        return None

    task = Task.query.get(command.task_id)
    if not task:
        _mark_business_exception(
            command,
            "Locker deposit loop closed but linked task was not found",
            actor_type=actor_type,
        )
        return None

    box = Box.query.get(command.box_no)
    if box and box.status != "occupied":
        box.status = "reserved"
        box.task_id = task.task_id

    already_waiting_ai = task.status == "waiting_ai"
    if task.status in {"assigned", "reassigned", "ai_failed"}:
        task.status = "waiting_ai"
        task.put_in_at = task.put_in_at or int(time.time())
        task.ai_checked_at = None
        task.ai_message = None
        mark_task_exception_resolved(task, "异常已处理完成，已重新完成入柜，等待系统确认")

    order = mark_order_waiting_ai(task)
    if not already_waiting_ai:
        log_action(
            "task.put_in_confirmed",
            actor_type="courier" if actor_type == "hardware" else actor_type,
            actor_openid=command.operator_id or command.box_no,
            platform_order_id=getattr(order, "platform_order_id", None),
            task_id=task.task_id,
            detail=f"Courier deposit locker loop completed on box {command.box_no}",
        )
        emit_platform_event("order_waiting_ai", order=order, task=task)

    _mark_business_completed(
        command,
        "Courier deposit loop closed. Task moved to waiting_ai for AI verification.",
        actor_type=actor_type,
    )
    return task


def _finalize_command_business(command: LockerCommand, actor_type: str = "hardware"):
    scene = _command_scene_from_command(command)
    if scene == "pickup":
        return _finalize_pickup_from_command(command, actor_type=actor_type)
    if scene == "deposit":
        return _finalize_deposit_from_command(command, actor_type=actor_type)

    _mark_business_completed(command, command.detail or _closed_loop_detail(command), actor_type=actor_type)
    return None


def mark_package_locker_business_state(
    *,
    package_id: str = None,
    command_id: str = None,
    status: str,
    note: str = "",
    actor_type: str = "system",
    actor_openid: str = None,
):
    command = LockerCommand.query.get(command_id) if command_id else _resolve_latest_package_command(package_id)
    if not command:
        return None

    if status == BUSINESS_COMPLETED:
        _mark_business_completed(command, note or "Locker business flow completed", actor_type=actor_type, actor_openid=actor_openid)
    elif status == BUSINESS_EXCEPTION:
        _mark_business_exception(command, note or "Locker business flow entered exception state", actor_type=actor_type, actor_openid=actor_openid)
    else:
        _set_business_state(command, status=status, note=note)

    db.session.flush()
    return command


def apply_locker_callback(payload, *, source="hardware"):
    box_no = (payload.get("boxNo") or payload.get("box_no") or "").strip()
    command_id = (payload.get("commandId") or payload.get("command_id") or "").strip()
    command = _resolve_command(command_id, box_no)
    if not command:
        return None, None, "Locker command not found"

    refresh_locker_timeouts(exclude_command_ids={command.command_id})

    event_type = _normalize_event_type(payload)
    detail = _trim_text(payload.get("detail") or payload.get("message"))
    door_state = _normalize_signal(payload.get("doorState") or payload.get("door_state"))
    lock_feedback_state = _normalize_signal(payload.get("lockFeedback") or payload.get("lock_feedback"))
    controller_power_state = _normalize_signal(payload.get("controllerPowerState") or payload.get("controller_power_state"))
    lock_power_state = _normalize_signal(payload.get("lockPowerState") or payload.get("lock_power_state"))

    if command.status in LOCKER_TERMINAL_STATUSES:
        current_app.logger.warning(
            "locker.callback.ignored_terminal command=%s box=%s event=%s status=%s source=%s",
            command.command_id,
            command.box_no,
            event_type,
            command.status,
            source,
        )
        return command, None, None

    if _command_has_event(command, event_type):
        current_app.logger.warning(
            "locker.callback.duplicate command=%s box=%s event=%s status=%s source=%s",
            command.command_id,
            command.box_no,
            event_type,
            command.status,
            source,
        )
        return command, None, None

    if event_type == "command_ack":
        detail = detail or "Locker device accepted the open-door command"
        _apply_command_update(
            command,
            status="accepted",
            accepted=True,
            pending_hardware=True,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            callback_payload=payload,
        )
        config = get_locker_integration_config()
        command.timeout_at = _utcnow() + timedelta(
            milliseconds=int(config.get("openTimeoutMs") or 15000)
        )
        _set_business_state(command, status=BUSINESS_PENDING, note=detail)
    elif event_type == "relay_triggered":
        detail = detail or "Relay pulse triggered"
        _apply_command_update(
            command,
            status="relay_triggered",
            accepted=True,
            pending_hardware=True,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            callback_payload=payload,
        )
    elif event_type == "lock_opened":
        detail = detail or "Lock feedback confirmed the door was unlocked"
        _apply_command_update(
            command,
            status="waiting_door_open",
            accepted=True,
            pending_hardware=True,
            lock_feedback_state=lock_feedback_state or "unlocked",
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            callback_payload=payload,
        )
        _set_business_state(command, status=BUSINESS_PENDING, note=detail)
    elif event_type == "door_opened":
        detail = detail or "Door sensor confirmed the door opened"
        _apply_command_update(
            command,
            status="door_opened",
            accepted=True,
            pending_hardware=True,
            door_state=door_state or "open",
            lock_feedback_state=lock_feedback_state,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            callback_payload=payload,
        )
        _set_business_state(
            command,
            status=BUSINESS_WAITING_CLOSE,
            note=_waiting_close_note(command),
        )
    elif event_type == "door_closed":
        if not command.door_opened_at:
            event_type = "sequence_error"
            detail = detail or "Door closed callback arrived before any door opened callback"
            _apply_command_update(
                command,
                status="sequence_error",
                accepted=True,
                pending_hardware=False,
                door_state=door_state or "closed",
                lock_feedback_state=lock_feedback_state,
                controller_power_state=controller_power_state,
                lock_power_state=lock_power_state,
                detail=detail,
                last_error=detail,
                callback_payload=payload,
            )
            _mark_business_exception(command, detail, actor_type=source)
        else:
            detail = detail or _closed_loop_detail(command)
            _apply_command_update(
                command,
                status="completed",
                accepted=True,
                pending_hardware=False,
                door_state=door_state or "closed",
                lock_feedback_state=lock_feedback_state,
                controller_power_state=controller_power_state,
                lock_power_state=lock_power_state,
                detail=detail,
                callback_payload=payload,
            )
            _finalize_command_business(command, actor_type=source if source in {"hardware", "system"} else "hardware")
    elif event_type in {"lock_failed", "command_failed", "device_offline"}:
        detail = detail or (
            "Locker hardware reported offline"
            if event_type == "device_offline"
            else "Locker command execution failed"
        )
        _apply_command_update(
            command,
            status="failed",
            accepted=True,
            pending_hardware=False,
            door_state=door_state,
            lock_feedback_state=lock_feedback_state or ("failed" if event_type != "device_offline" else None),
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            last_error=detail,
            callback_payload=payload,
        )
        _mark_business_exception(command, detail, actor_type=source)
    elif event_type == "open_timeout":
        detail = detail or "No lock feedback or door-open callback arrived before timeout"
        _apply_command_update(
            command,
            status="open_timeout",
            accepted=True,
            pending_hardware=False,
            door_state=door_state,
            lock_feedback_state=lock_feedback_state,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            last_error=detail,
            callback_payload=payload,
        )
        _mark_business_exception(command, detail, actor_type=source)
    elif event_type == "door_close_timeout":
        detail = detail or "Door was opened but did not close before timeout"
        _apply_command_update(
            command,
            status="door_close_timeout",
            accepted=True,
            pending_hardware=False,
            door_state=door_state or "open",
            lock_feedback_state=lock_feedback_state,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            last_error=detail,
            callback_payload=payload,
        )
        _mark_business_exception(command, detail, actor_type=source)
    else:
        detail = detail or f"Received locker event {event_type}"
        _apply_command_update(
            command,
            door_state=door_state,
            lock_feedback_state=lock_feedback_state,
            controller_power_state=controller_power_state,
            lock_power_state=lock_power_state,
            detail=detail,
            callback_payload=payload,
        )
        if _command_open_succeeded(command) and not command.door_closed_at:
            _set_business_state(command, status=BUSINESS_WAITING_CLOSE, note=_waiting_close_note(command))

    event = _record_locker_event(
        command=command,
        box_no=command.box_no,
        event_type=event_type,
        source=source,
        detail=detail or command.detail,
        raw_payload=payload,
    )

    log_meta = _event_log_meta(event_type)
    if log_meta:
        action, default_detail = log_meta
        log_action(
            action,
            actor_type="hardware",
            actor_openid=command.box_no,
            task_id=command.task_id,
            package_id=command.package_id,
            detail=detail or default_detail,
        )

    current_app.logger.info(
        "locker.callback.applied command=%s box=%s event=%s status_after=%s source=%s",
        command.command_id,
        command.box_no,
        event_type,
        command.status,
        source,
    )

    db.session.flush()
    return command, event, None


def refresh_locker_timeouts(now=None, exclude_command_ids=None):
    current_time = now or _utcnow()
    excluded = set(exclude_command_ids or [])
    updated = []
    commands = (
        LockerCommand.query
        .filter(LockerCommand.status.in_(list(LOCKER_ACTIVE_STATUSES)))
        .all()
    )

    for command in commands:
        if command.command_id in excluded:
            continue
        if command.status == "door_opened":
            if command.door_close_timeout_at and current_time > command.door_close_timeout_at:
                detail = "Door remained open beyond the configured close timeout"
                _apply_command_update(
                    command,
                    status="door_close_timeout",
                    pending_hardware=False,
                    detail=detail,
                    last_error=detail,
                )
                _mark_business_exception(command, detail, actor_type="system")
                _record_locker_event(
                    command=command,
                    box_no=command.box_no,
                    event_type="door_close_timeout",
                    source="system",
                    detail=detail,
                    raw_payload={"source": "timeout_guard"},
                )
                log_action(
                    "locker.door_close_timeout",
                    actor_type="system",
                    actor_openid=command.box_no,
                    task_id=command.task_id,
                    package_id=command.package_id,
                    detail=detail,
                )
                updated.append(command.command_id)
            continue

        if command.timeout_at and current_time > command.timeout_at:
            detail = "No lock or door feedback arrived before timeout"
            if command.status == "waiting_door_open" or command.lock_opened_at:
                detail = "Unlock feedback arrived, but no door-open callback arrived before timeout"
            _apply_command_update(
                command,
                status="open_timeout",
                pending_hardware=False,
                detail=detail,
                last_error=detail,
            )
            _mark_business_exception(command, detail, actor_type="system")
            _record_locker_event(
                command=command,
                box_no=command.box_no,
                event_type="open_timeout",
                source="system",
                detail=detail,
                raw_payload={"source": "timeout_guard"},
            )
            log_action(
                "locker.command_timeout",
                actor_type="system",
                actor_openid=command.box_no,
                task_id=command.task_id,
                package_id=command.package_id,
                detail=detail,
            )
            updated.append(command.command_id)

    if updated:
        db.session.commit()
    return updated


def claim_executable_locker_command(box_no: str, *, device_id: str = ""):
    refresh_locker_timeouts()
    command = _resolve_latest_executable_box_command(box_no)
    if not command:
        current_app.logger.info(
            "locker.next_command.claim box=%s device=%s result=null",
            box_no,
            device_id or "-",
        )
        return None

    if not command.accepted:
        detail = f"Locker device {device_id or box_no} fetched pending command"
        current_app.logger.info(
            "locker.next_command.claim box=%s device=%s command=%s status_before=%s",
            command.box_no,
            device_id or "-",
            command.command_id,
            command.status,
        )
        _apply_command_update(
            command,
            status="accepted",
            accepted=True,
            pending_hardware=True,
            detail=detail,
            callback_payload={"deviceId": device_id or None, "source": "device_poll"},
        )
        config = get_locker_integration_config()
        command.timeout_at = _utcnow() + timedelta(
            milliseconds=int(config.get("openTimeoutMs") or 15000)
        )
        _set_business_state(command, status=BUSINESS_PENDING, note=detail)
        _record_locker_event(
            command=command,
            box_no=command.box_no,
            event_type="command_ack",
            source="hardware",
            detail=detail,
            raw_payload={"deviceId": device_id or None, "source": "device_poll"},
        )
        log_action(
            "locker.command_ack",
            actor_type="hardware",
            actor_openid=device_id or command.box_no,
            task_id=command.task_id,
            package_id=command.package_id,
            detail=detail,
        )

    db.session.flush()
    return command


def serialize_locker_command(command: LockerCommand, *, include_events=False, event_limit=10):
    if not command:
        return None

    _ensure_command_trace_defaults(command)
    sync_command_trace_meta(command)
    payload = command.to_dict()
    scene = _command_scene_from_command(command)
    labels = _command_trace_labels(command, scene=scene)
    archive_task_id = _command_trace_task_id(command)
    payload["taskId"] = _clean_identifier(payload.get("taskId")) or archive_task_id
    payload["packageId"] = _clean_identifier(payload.get("packageId"))
    payload["commandScene"] = scene
    payload["commandSceneLabel"] = labels.get("sceneLabel") or _command_scene_label(scene)
    payload["archiveDirectory"] = _command_trace_directory(command, scene=scene)
    payload["snapshotLabel"] = labels.get("snapshotLabel")
    payload["resultImageLabel"] = labels.get("resultImageLabel")
    payload["videoLabel"] = labels.get("videoLabel")
    payload["openImageLabel"] = labels.get("openImageLabel")
    payload["isExecutable"] = bool(
        command.pending_hardware
        and command.finished_at is None
        and command.status in LOCKER_EXECUTABLE_STATUSES
        and not command.relay_triggered_at
        and not command.lock_opened_at
        and not command.door_opened_at
        and not command.door_closed_at
    )
    payload["completed"] = command.status == "completed"
    payload["businessCompleted"] = command.business_status == BUSINESS_COMPLETED
    payload["hasAlert"] = command.status in LOCKER_ALERT_STATUSES or command.business_status == BUSINESS_EXCEPTION
    payload["needsManualIntervention"] = bool(payload["hasAlert"] and not payload["businessCompleted"])
    if include_events:
        events = (
            LockerEvent.query
            .filter_by(command_id=command.command_id)
            .order_by(LockerEvent.created_at.desc(), LockerEvent.event_id.desc())
            .limit(event_limit)
            .all()
        )
        payload["events"] = [event.to_dict() for event in events]
    return payload


def list_recent_locker_commands(*, limit=20, box_no=None, status=None):
    refresh_locker_timeouts()
    query = LockerCommand.query
    if box_no:
        query = query.filter_by(box_no=box_no)
    if status:
        query = query.filter_by(status=status)
    return (
        query.order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .limit(limit)
        .all()
    )


def list_recent_locker_events(*, limit=20, box_no=None, command_id=None, event_types=None, source=None):
    refresh_locker_timeouts()
    query = LockerEvent.query
    if box_no:
        query = query.filter_by(box_no=box_no)
    if command_id:
        query = query.filter_by(command_id=command_id)
    if source:
        query = query.filter_by(source=source)
    if event_types:
        query = query.filter(LockerEvent.event_type.in_(list(event_types)))
    return (
        query.order_by(LockerEvent.created_at.desc(), LockerEvent.event_id.desc())
        .limit(limit)
        .all()
    )


def get_latest_locker_event(*, box_no=None, command_id=None, event_types=None):
    query = LockerEvent.query
    if box_no:
        query = query.filter_by(box_no=box_no)
    if command_id:
        query = query.filter_by(command_id=command_id)
    if event_types:
        query = query.filter(LockerEvent.event_type.in_(list(event_types)))
    return (
        query.order_by(LockerEvent.created_at.desc(), LockerEvent.event_id.desc())
        .first()
    )


def get_locker_command(command_id: str):
    refresh_locker_timeouts()
    return LockerCommand.query.get(command_id)


def get_box_locker_status(box_no: str, *, event_limit=5, refresh=True):
    if refresh:
        refresh_locker_timeouts()
    config = get_locker_integration_config()
    pending_command = _resolve_latest_executable_box_command(box_no)
    latest_command = (
        LockerCommand.query
        .filter_by(box_no=box_no)
        .order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )
    recent_events = (
        LockerEvent.query
        .filter_by(box_no=box_no)
        .order_by(LockerEvent.created_at.desc(), LockerEvent.event_id.desc())
        .limit(event_limit)
        .all()
    )
    latest_payload = serialize_locker_command(latest_command)
    latest_lock_event = get_latest_locker_event(box_no=box_no, event_types=LOCKER_LOCK_EVENT_TYPES)
    latest_door_event = get_latest_locker_event(box_no=box_no, event_types=LOCKER_DOOR_EVENT_TYPES)
    latest_alert_event = get_latest_locker_event(box_no=box_no, event_types=LOCKER_ALERT_EVENT_TYPES)

    return {
        "boxNo": box_no,
        "hardwareProfile": config.get("hardwareProfile"),
        "powerTopology": config.get("powerTopology"),
        "lockPowerSource": config.get("lockPowerSource"),
        "controllerPowerSource": config.get("controllerPowerSource"),
        "pendingCommand": serialize_locker_command(pending_command) if pending_command else None,
        "latestCommand": latest_payload,
        "doorState": latest_command.door_state if latest_command else None,
        "lockFeedbackState": latest_command.lock_feedback_state if latest_command else None,
        "controllerPowerState": latest_command.controller_power_state if latest_command else None,
        "lockPowerState": latest_command.lock_power_state if latest_command else None,
        "pendingHardware": bool(latest_command and latest_command.pending_hardware),
        "hasAlert": bool(latest_payload and latest_payload.get("hasAlert")),
        "businessStatus": latest_payload.get("businessStatus") if latest_payload else None,
        "businessNote": latest_payload.get("businessNote") if latest_payload else None,
        "latestLockEvent": latest_lock_event.to_dict() if latest_lock_event else None,
        "latestDoorEvent": latest_door_event.to_dict() if latest_door_event else None,
        "latestAlertEvent": latest_alert_event.to_dict() if latest_alert_event else None,
        "recentEvents": [event.to_dict() for event in recent_events],
    }


def get_box_locker_status_map(box_nos):
    refresh_locker_timeouts()
    return {box_no: get_box_locker_status(box_no, refresh=False) for box_no in box_nos}


def request_open_door(
    box_no: str,
    *,
    operator_type: str,
    operator_id: str = "",
    reason: str = "",
    task_id: str = None,
    package_id: str = None,
):
    refresh_locker_timeouts()

    config = get_locker_integration_config()
    controller_url = (config.get("controllerUrl") or "").strip()
    mode = config["mode"]
    effective_mode = "mock" if mode == "mock" else "hardware"
    fallback_used = False
    now = _utcnow()
    scene = _command_scene_from_values(operator_type, reason)

    active_command = _resolve_latest_active_box_command(box_no)
    if active_command:
        reused_existing = _matches_command_context(
            active_command,
            scene=scene,
            task_id=task_id,
            package_id=package_id,
        )
        active_payload = serialize_locker_command(active_command)
        active_scene = active_payload.get("commandScene") or _command_scene_from_command(active_command)
        if reused_existing:
            note = "An active locker command already exists for this business flow. Reusing the current command."
        else:
            note = f"Box {box_no} already has an active {active_scene} locker command in progress."
        active_payload.update({
            "accepted": bool(active_command.accepted),
            "completed": active_command.status == "completed",
            "mode": active_command.mode,
            "effectiveMode": active_command.mode,
            "controllerName": active_command.controller_name or config["providerName"],
            "controllerUrl": active_command.controller_url or controller_url,
            "fallbackUsed": bool(active_command.fallback_used),
            "reusedExistingCommand": reused_existing,
            "conflict": not reused_existing,
            "note": note,
        })
        current_app.logger.info(
            "locker.command.reuse box=%s command=%s scene=%s reused=%s conflict=%s",
            box_no,
            active_command.command_id,
            active_scene,
            reused_existing,
            not reused_existing,
        )
        return active_payload

    if effective_mode == "mock":
        detail = "Mock locker command created. Current demo flow can continue without hardware."
    else:
        detail = "Hardware locker command created. Waiting for ESP32 to poll the pending command, unlock, and report door sensor callbacks."

    command = LockerCommand(
        command_id=_gen_command_id("DOOR"),
        box_no=box_no,
        task_id=task_id,
        package_id=package_id,
        operator_type=operator_type,
        operator_id=(operator_id or "").strip() or None,
        reason=(reason or "").strip() or None,
        mode=mode,
        controller_name=config.get("providerName"),
        controller_url=controller_url or None,
        hardware_profile=config.get("hardwareProfile"),
        power_topology=config.get("powerTopology"),
        status="accepted" if effective_mode == "mock" else "created",
        business_status=BUSINESS_PENDING,
        accepted=effective_mode == "mock",
        fallback_used=fallback_used,
        pending_hardware=effective_mode == "hardware",
        detail=_trim_text(detail),
        business_note=_trim_text(detail),
        request_payload=_dump_json({
            "boxNo": box_no,
            "operatorType": operator_type,
            "operatorId": operator_id,
            "reason": reason,
            "taskId": task_id,
            "packageId": package_id,
            "commandScene": scene,
            "mode": mode,
            "effectiveMode": effective_mode,
        }),
        timeout_at=(now + timedelta(milliseconds=int(config.get("openTimeoutMs") or 15000))) if effective_mode == "mock" else None,
        controller_power_state="ok" if effective_mode == "mock" else None,
        lock_power_state="ok" if effective_mode == "mock" else None,
        accepted_at=now if effective_mode == "mock" else None,
    )
    _ensure_command_trace_defaults(command)
    db.session.add(command)

    _record_locker_event(
        command=command,
        box_no=box_no,
        event_type="command_created",
        source="backend",
        detail="Backend created locker open-door command",
        raw_payload={
            "operatorType": operator_type,
            "operatorId": operator_id,
            "reason": reason,
            "taskId": task_id,
            "packageId": package_id,
        },
    )
    log_action(
        "locker.command_created",
        actor_type=operator_type if operator_type in {"admin", "courier", "user"} else "system",
        actor_openid=operator_id or box_no,
        task_id=task_id,
        package_id=package_id,
        detail="Backend created locker open-door command",
    )

    if effective_mode == "mock":
        ack_detail = (
            "Mock locker command acknowledged"
            if effective_mode == "mock"
            else "Hardware locker command accepted and waiting controller callback"
        )
        _record_locker_event(
            command=command,
            box_no=box_no,
            event_type="command_ack",
            source="backend",
            detail=ack_detail,
            raw_payload={"effectiveMode": effective_mode, "fallbackUsed": fallback_used},
        )
        log_action(
            "locker.command_ack",
            actor_type="system",
            actor_openid=box_no,
            task_id=task_id,
            package_id=package_id,
            detail=ack_detail,
        )

    if effective_mode == "mock" and config.get("mockAutoComplete"):
        _apply_command_update(
            command,
            status="relay_triggered",
            accepted=True,
            pending_hardware=False,
            controller_power_state="ok",
            lock_power_state="ok",
            detail="Mock relay triggered",
        )
        _record_locker_event(
            command=command,
            box_no=box_no,
            event_type="relay_triggered",
            source="mock",
            detail="Mock relay triggered",
            raw_payload={"source": "mock"},
        )

        _apply_command_update(
            command,
            status="waiting_door_open",
            lock_feedback_state="unlocked",
            detail="Mock lock feedback reported unlocked",
        )
        _record_locker_event(
            command=command,
            box_no=box_no,
            event_type="lock_opened",
            source="mock",
            detail="Mock lock feedback reported unlocked",
            raw_payload={"source": "mock"},
        )

        _apply_command_update(
            command,
            status="door_opened",
            door_state="open",
            detail="Mock door sensor reported opened",
        )
        _set_business_state(
            command,
            status=BUSINESS_WAITING_CLOSE,
            note=_waiting_close_note(command),
        )
        _record_locker_event(
            command=command,
            box_no=box_no,
            event_type="door_opened",
            source="mock",
            detail="Mock door sensor reported opened",
            raw_payload={"source": "mock"},
        )

        _apply_command_update(
            command,
            status="completed",
            door_state="closed",
            detail="Mock door sensor reported closed",
        )
        _record_locker_event(
            command=command,
            box_no=box_no,
            event_type="door_closed",
            source="mock",
            detail="Mock door sensor reported closed",
            raw_payload={"source": "mock"},
        )
        _finalize_command_business(command, actor_type="system")

    db.session.flush()
    response = serialize_locker_command(command)
    current_app.logger.info(
        "locker.command.created box=%s command=%s scene=%s mode=%s pending_hardware=%s task=%s package=%s",
        command.box_no,
        command.command_id,
        scene,
        effective_mode,
        bool(command.pending_hardware),
        command.task_id or "-",
        command.package_id or "-",
    )
    response.update({
        "accepted": bool(command.accepted),
        "completed": command.status == "completed",
        "mode": mode,
        "effectiveMode": effective_mode,
        "controllerName": config["providerName"],
        "controllerUrl": controller_url,
        "fallbackUsed": fallback_used,
        "conflict": False,
        "reusedExistingCommand": False,
        "note": detail,
    })
    return response


def handle_locker_callback(payload):
    command, event, error = apply_locker_callback(payload, source="hardware")
    if error:
        return {"status": "error", "message": error, "payload": payload}

    db.session.commit()
    return {
        "status": "success",
        "message": "Locker callback received",
        "command": serialize_locker_command(command),
        "event": event.to_dict() if event else None,
    }


def emit_hardware_event(event_name: str, order=None, task=None, pkg=None):
    config = get_locker_integration_config()
    controller_url = (config.get("controllerUrl") or "").strip()
    simulated = config["mode"] == "mock"

    return {
        "event": event_name,
        "mode": config["mode"],
        "controllerName": config["providerName"],
        "controllerUrl": controller_url,
        "hardwareProfile": config.get("hardwareProfile"),
        "powerTopology": config.get("powerTopology"),
        "lockPowerSource": config.get("lockPowerSource"),
        "controllerPowerSource": config.get("controllerPowerSource"),
        "simulated": simulated,
        "platformOrderId": getattr(order, "platform_order_id", None),
        "taskId": getattr(task, "task_id", None),
        "packageId": getattr(pkg, "package_id", None),
        "note": "Locker integration skeleton keeps split-power hardware topology and callback hooks ready for later device testing.",
    }
