import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def parse_args():
    parser = argparse.ArgumentParser(description="Run YOLOv5 on one locker snapshot and print JSON result.")
    parser.add_argument("--image", required=True, help="Absolute path of snapshot image.")
    parser.add_argument("--weights", required=True, help="YOLOv5 weight file path.")
    parser.add_argument("--yolo-root", required=True, help="YOLOv5 repository root.")
    parser.add_argument("--conf-thres", default="0.25", help="Confidence threshold.")
    parser.add_argument("--imgsz", default="640", help="Image size.")
    return parser.parse_args()


def subprocess_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def build_output_dir(repo_root: Path) -> Path:
    project_root = repo_root.parent
    output_root = project_root / "runtime" / "inference_runs"
    output_root.mkdir(parents=True, exist_ok=True)
    run_suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_name = f"snapshot_detect_{run_suffix}_{uuid4().hex[:8]}"
    return output_root / run_name


def label_file_for(output_dir: Path, image_path: Path) -> Path:
    return output_dir / "labels" / f"{image_path.stem}.txt"


def parse_labels(label_path: Path):
    if not label_path.exists():
        return []

    detections = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        item = {
            "classId": parts[0],
            "bbox": parts[1:5],
        }
        if len(parts) >= 6:
            item["confidence"] = parts[5]
        detections.append(item)
    return detections


def classify_runtime_failure(stdout: str, stderr: str) -> str:
    message = f"{stdout}\n{stderr}".lower()
    dependency_markers = [
        "modulenotfounderror",
        "no module named",
        "importerror",
        "cannot import name",
        "dll load failed",
        "torch",
    ]
    if any(marker in message for marker in dependency_markers):
        return "dependency_missing"
    return "script_failed"


def main():
    args = parse_args()
    image_path = Path(args.image).resolve()
    weights_path = Path(args.weights).resolve()
    yolo_root = Path(args.yolo_root).resolve()
    detect_script = yolo_root / "detect.py"
    output_dir = build_output_dir(yolo_root)

    if not image_path.exists():
        print_json({
            "ok": False,
            "detail": f"Snapshot not found: {image_path}",
            "detectorStatus": "image_missing",
        })
        return 1

    if not weights_path.exists():
        print_json({
            "ok": False,
            "detail": f"Weights not found: {weights_path}",
            "detectorStatus": "weights_missing",
        })
        return 1

    if not detect_script.exists():
        print_json({
            "ok": False,
            "detail": f"detect.py not found under: {yolo_root}",
            "detectorStatus": "detect_script_missing",
        })
        return 1

    command = [
        sys.executable,
        str(detect_script),
        "--weights",
        str(weights_path),
        "--source",
        str(image_path),
        "--conf-thres",
        str(args.conf_thres),
        "--imgsz",
        str(args.imgsz),
        "--save-txt",
        "--save-conf",
        "--project",
        str(output_dir.parent),
        "--name",
        output_dir.name,
    ]

    result = subprocess.run(
        command,
        cwd=str(yolo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=subprocess_env(),
        check=False,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    label_path = label_file_for(output_dir, image_path)
    detections = parse_labels(label_path)

    payload = {
        "ok": False,
        "imagePath": str(image_path),
        "weightsPath": str(weights_path),
        "outputDir": str(output_dir),
        "labelsPath": str(label_path),
        "detections": detections,
        "stdout": stdout,
        "stderr": stderr,
    }

    if result.returncode != 0:
        detector_status = classify_runtime_failure(stdout, stderr)
        payload["detectorStatus"] = detector_status
        payload["detail"] = (
            "YOLOv5 运行环境缺失或依赖未安装。"
            if detector_status == "dependency_missing"
            else "YOLOv5 detect.py 执行失败。"
        )
        print_json(payload)
        return result.returncode

    if detections:
        payload["ok"] = True
        payload["detectorStatus"] = "ok"
        payload["detail"] = "检测到包裹候选目标，判定入柜成功。"
    else:
        payload["detectorStatus"] = "no_target_detected"
        payload["detail"] = "YOLOv5 执行成功，但未检测到包裹目标。"

    print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
