import base64
import binascii
import os
import uuid

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Box, Package, PlatformOrder, Task
from services.ai_gateway import RUNTIME_ERROR_STATUSES, handle_ai_callback, request_box_detection
from services.integration_runtime import get_integration_status_payload
from services.locker_gateway import (
    LOCKER_ALERT_EVENT_TYPES,
    LOCKER_DOOR_EVENT_TYPES,
    LOCKER_LOCK_EVENT_TYPES,
    build_command_artifact_relative_path,
    claim_executable_locker_command,
    get_box_locker_status,
    get_locker_command,
    handle_locker_callback,
    list_recent_locker_events,
    list_recent_locker_commands,
    refresh_locker_timeouts,
    request_open_door,
    serialize_locker_command,
    serialize_locker_trace,
    sync_command_trace_meta,
)
from services.platform_flow import (
    emit_platform_event,
    get_latest_task_for_order,
    get_order_status_view,
    get_task_stage,
)
from services.platform_gateway import (
    PlatformPayloadError,
    upsert_platform_order_from_payload,
    verify_platform_request_signature,
)
from utils.time_utils import project_now

integration_bp = Blueprint("integration", __name__)


def _artifact_root():
    root = (current_app.config.get("ARTIFACTS_DIR") or "").strip()
    if not root:
        root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "artifacts")
    os.makedirs(root, exist_ok=True)
    return os.path.abspath(root)


def _artifact_url_prefix():
    return (current_app.config.get("ARTIFACTS_URL_PREFIX") or "/artifacts").rstrip("/")


def _artifact_public_url(relative_path: str):
    if not relative_path:
        return None
    clean = relative_path.replace("\\", "/").lstrip("/")
    if clean.startswith("artifacts/"):
        clean = clean[len("artifacts/"):]
    return f"{_artifact_url_prefix()}/{clean}"


def _dataset_raw_root():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root = os.path.join(repo_root, "data", "raw", "esp32_capture")
    os.makedirs(root, exist_ok=True)
    return root


def _dataset_raw_relative_path(filename: str) -> str:
    return f"data/raw/esp32_capture/{filename}"


def _read_uploaded_image_bytes():
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        data = image_file.read()
        return data, "multipart"

    raw_bytes = request.get_data(cache=True, as_text=False) or b""
    content_type = (request.content_type or "").lower()
    if raw_bytes and ("image/jpeg" in content_type or "application/octet-stream" in content_type):
        return raw_bytes, "raw_binary"

    payload = request.get_json(silent=True) or {}
    image_b64 = (payload.get("imageBase64") or payload.get("image_base64") or "").strip()
    if not image_b64:
        return None, None

    if "," in image_b64 and image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]

    try:
        return base64.b64decode(image_b64, validate=True), "base64"
    except (binascii.Error, ValueError):
        return None, "invalid_base64"


def _build_order_view(order: PlatformOrder):
    task = get_latest_task_for_order(order.platform_order_id)
    pkg = Package.query.get(task.package_id) if task and task.package_id else None

    item = order.to_dict()
    item["task"] = task.to_dict() if task else None
    item["package"] = pkg.to_dict() if pkg else None
    item["statusView"] = get_order_status_view(order, task, pkg)
    item["taskStage"] = get_task_stage(task) if task else None
    return item


@integration_bp.get("/api/integration/status")
def integration_status():
    refresh_locker_timeouts()
    return jsonify({"status": "success", **get_integration_status_payload()})


@integration_bp.post("/api/integration/platform/push")
def reserved_platform_push():
    verified, message = verify_platform_request_signature(request)
    if not verified:
        return jsonify({"status": "error", "message": message}), 401

    payload = request.get_json(silent=True) or {}
    try:
        order, created, normalized = upsert_platform_order_from_payload(
            payload,
            source="platform_webhook",
            provider_name=(request.headers.get("X-Platform-Name") or "").strip() or "platform-webhook",
            actor_type="platform",
            actor_openid=(request.headers.get("X-Platform-Name") or "").strip() or "platform-webhook",
        )
    except PlatformPayloadError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
            "reasonCode": exc.reason_code,
        }), exc.http_code
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    db.session.commit()
    emit_platform_event("order_received", order=order)

    return jsonify({
        "status": "success",
        "created": created,
        "mode": "integration_reserved",
        "normalized": normalized,
        "order": _build_order_view(order),
    })


@integration_bp.post("/api/integration/locker/open_door")
def reserved_open_door():
    payload = request.get_json(silent=True) or {}
    box_no = (payload.get("boxNo") or payload.get("box_no") or "").strip()
    if not box_no:
        return jsonify({"status": "error", "message": "Missing boxNo"}), 400

    result = request_open_door(
        box_no,
        operator_type=(payload.get("operatorType") or payload.get("operator_type") or "system").strip() or "system",
        operator_id=(payload.get("operatorId") or payload.get("operator_id") or "").strip(),
        reason=(payload.get("reason") or "manual_test").strip(),
        task_id=(payload.get("taskId") or payload.get("task_id") or "").strip() or None,
        package_id=(payload.get("packageId") or payload.get("package_id") or "").strip() or None,
    )
    db.session.commit()

    if result.get("conflict") and not result.get("reusedExistingCommand"):
        http_code = 409
    elif result.get("pendingHardware") or result.get("accepted"):
        http_code = 200
    else:
        http_code = 503
    return jsonify({
        "status": "success" if http_code == 200 else "error",
        "result": result,
    }), http_code


@integration_bp.post("/api/integration/locker/callback")
def reserved_locker_callback():
    payload = request.get_json(silent=True) or {}
    result = handle_locker_callback(payload)
    return jsonify(result), (200 if result.get("status") == "success" else 404)


@integration_bp.post("/api/integration/locker/upload_snapshot")
def locker_upload_snapshot():
    form = request.form or {}
    payload = request.get_json(silent=True) or {}
    args = request.args or {}

    command_id = (
        form.get("commandId")
        or args.get("commandId")
        or args.get("command_id")
        or request.headers.get("X-Command-Id")
        or payload.get("commandId")
        or payload.get("command_id")
        or ""
    ).strip()
    box_no = (
        form.get("boxNo")
        or args.get("boxNo")
        or args.get("box_no")
        or request.headers.get("X-Box-No")
        or payload.get("boxNo")
        or payload.get("box_no")
        or ""
    ).strip()
    task_id = (
        form.get("taskId")
        or args.get("taskId")
        or args.get("task_id")
        or request.headers.get("X-Task-Id")
        or payload.get("taskId")
        or payload.get("task_id")
        or ""
    ).strip() or None
    package_id = (
        form.get("packageId")
        or args.get("packageId")
        or args.get("package_id")
        or request.headers.get("X-Package-Id")
        or payload.get("packageId")
        or payload.get("package_id")
        or ""
    ).strip() or None
    scene = (
        form.get("scene")
        or args.get("scene")
        or request.headers.get("X-Scene")
        or payload.get("scene")
        or ""
    ).strip().lower() or "manual"
    kind = (
        form.get("kind")
        or args.get("kind")
        or request.headers.get("X-Kind")
        or payload.get("kind")
        or ""
    ).strip().lower() or "snapshot"

    if not command_id or not box_no:
        return jsonify({"status": "error", "message": "Missing commandId or boxNo"}), 400

    command = get_locker_command(command_id)
    if not command:
        return jsonify({"status": "error", "message": "Locker command not found"}), 404
    if command.box_no != box_no:
        return jsonify({"status": "error", "message": "boxNo does not match the locker command"}), 409

    image_bytes, source = _read_uploaded_image_bytes()
    if source == "invalid_base64":
        return jsonify({"status": "error", "message": "Invalid imageBase64"}), 400
    if not image_bytes:
        return jsonify({"status": "error", "message": "Missing image payload"}), 400

    if task_id and not command.task_id:
        command.task_id = task_id
    if package_id and not command.package_id:
        command.package_id = package_id

    command.recording_scene = scene or command.recording_scene or "manual"
    if command.recording_scene == "deposit":
        command.ai_participates = True
    elif command.recording_scene == "pickup":
        command.ai_participates = False

    result_kinds = {"result_image", "result", "ai_result", "open_frame", "open_snapshot", "open_image"}
    normalized_kind = "result_image" if kind in result_kinds else "snapshot"
    relative_path = build_command_artifact_relative_path(
        command,
        kind=normalized_kind,
        scene=command.recording_scene,
    )
    file_path = os.path.join(_artifact_root(), *relative_path.split("/"))
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as fp:
        fp.write(image_bytes)

    if normalized_kind == "result_image":
        command.result_image_path = relative_path
        if not command.result_image_status or command.result_image_status in {"waiting_ai", "pending", "not_required", "unavailable", "ai_failed"}:
            command.result_image_status = "ready"
    else:
        command.snapshot_path = relative_path
        command.snapshot_status = "captured"

    sync_command_trace_meta(command)
    db.session.commit()

    trace = serialize_locker_trace(command, audience="admin")
    return jsonify({
        "status": "success",
        "message": "Artifact uploaded",
        "source": source,
        "savedPath": relative_path,
        "savedUrl": _artifact_public_url(relative_path),
        "trace": trace,
    })


@integration_bp.post("/api/dataset/upload_raw")
def dataset_upload_raw():
    image_bytes, source = _read_uploaded_image_bytes()
    if source == "invalid_base64":
        return jsonify({"status": "error", "message": "Invalid imageBase64"}), 400
    if not image_bytes:
        return jsonify({"status": "error", "message": "Missing image payload"}), 400

    now = project_now()
    filename = f"esp32-raw-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jpg"
    save_root = _dataset_raw_root()
    file_path = os.path.join(save_root, filename)

    with open(file_path, "wb") as fp:
        fp.write(image_bytes)

    return jsonify({
        "status": "success",
        "savedPath": _dataset_raw_relative_path(filename),
        "fileSize": len(image_bytes),
        "source": source,
    })


@integration_bp.get("/api/integration/locker/commands")
def locker_commands():
    limit_raw = (request.args.get("limit") or "20").strip()
    box_no = (request.args.get("boxNo") or request.args.get("box_no") or "").strip() or None
    status = (request.args.get("status") or "").strip() or None
    try:
        limit = max(1, min(int(limit_raw), 100))
    except Exception:
        limit = 20

    commands = list_recent_locker_commands(limit=limit, box_no=box_no, status=status)
    return jsonify({
        "status": "success",
        "commands": [serialize_locker_command(command) for command in commands],
    })


@integration_bp.get("/api/integration/locker/events")
def locker_events():
    limit_raw = (request.args.get("limit") or "20").strip()
    box_no = (request.args.get("boxNo") or request.args.get("box_no") or "").strip() or None
    command_id = (request.args.get("commandId") or request.args.get("command_id") or "").strip() or None
    event_group = (request.args.get("group") or "all").strip().lower()
    source = (request.args.get("source") or "").strip() or None

    try:
        limit = max(1, min(int(limit_raw), 100))
    except Exception:
        limit = 20

    event_types = None
    if event_group == "lock":
        event_types = LOCKER_LOCK_EVENT_TYPES
    elif event_group == "door":
        event_types = LOCKER_DOOR_EVENT_TYPES
    elif event_group == "alert":
        event_types = LOCKER_ALERT_EVENT_TYPES

    events = list_recent_locker_events(
        limit=limit,
        box_no=box_no,
        command_id=command_id,
        event_types=event_types,
        source=source,
    )
    return jsonify({
        "status": "success",
        "group": event_group,
        "events": [event.to_dict() for event in events],
    })


@integration_bp.get("/api/integration/locker/commands/<string:command_id>")
def locker_command_detail(command_id):
    command = get_locker_command(command_id)
    if not command:
        return jsonify({"status": "error", "message": "Locker command not found"}), 404

    return jsonify({
        "status": "success",
        "command": serialize_locker_command(command, include_events=True),
    })


@integration_bp.get("/api/integration/locker/boxes/<string:box_no>")
def locker_box_detail(box_no):
    box = Box.query.get(box_no)
    if not box:
        return jsonify({"status": "error", "message": "Box not found"}), 404

    return jsonify({
        "status": "success",
        "box": box.to_dict(),
        "hardware": get_box_locker_status(box_no, event_limit=10),
    })


@integration_bp.get("/api/integration/locker/boxes/<string:box_no>/pending_command")
@integration_bp.get("/api/integration/locker/boxes/<string:box_no>/next_command")
def locker_box_pending_command(box_no):
    box = Box.query.get(box_no)
    if not box:
        return jsonify({"status": "error", "message": "Box not found"}), 404

    device_id = (request.args.get("deviceId") or request.args.get("device_id") or "").strip()
    command = claim_executable_locker_command(box_no, device_id=device_id)
    command_payload = serialize_locker_command(command) if command else None
    if command_payload:
        command_payload["deviceMayExecute"] = True
        command_payload["claimMode"] = "claim_once"
        current_app.logger.info(
            "locker.next_command box=%s device=%s result=%s",
            box_no,
            device_id or "-",
            command_payload.get("commandId"),
        )
    else:
        current_app.logger.info(
            "locker.next_command box=%s device=%s result=null",
            box_no,
            device_id or "-",
        )
    db.session.commit()

    return jsonify({
        "status": "success",
        "deviceId": device_id or None,
        "pollMode": "claim_once",
        "box": box.to_dict(),
        "lockerStatus": get_box_locker_status(box_no, event_limit=10),
        "command": command_payload,
    })


@integration_bp.post("/api/integration/locker/refresh_timeouts")
def locker_refresh_timeouts():
    updated = refresh_locker_timeouts()
    db.session.commit()
    return jsonify({
        "status": "success",
        "updatedCommandIds": updated,
    })


@integration_bp.post("/api/integration/ai/detect")
def reserved_ai_detect():
    payload = request.get_json(silent=True) or {}
    task = None
    box = None

    task_id = (payload.get("taskId") or payload.get("task_id") or "").strip()
    box_no = (payload.get("boxNo") or payload.get("box_no") or "").strip()

    if task_id:
        task = Task.query.get(task_id)
        if not task:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        box = Box.query.get(task.box_no)

    if not box and box_no:
        box = Box.query.get(box_no)

    if not box:
        return jsonify({"status": "error", "message": "Missing or invalid box reference"}), 400

    detection = request_box_detection(task, box)
    detector_status = (detection.get("detectorStatus") or "").strip()
    is_retryable = bool(detection.get("retryable")) or detector_status == "snapshot_pending"
    http_code = 202 if is_retryable else (503 if detector_status in RUNTIME_ERROR_STATUSES else 200)
    return jsonify({
        "status": "pending" if is_retryable else ("success" if http_code == 200 else "error"),
        "retryable": is_retryable,
        "message": detection.get("message") if is_retryable else None,
        "result": detection,
    }), http_code


@integration_bp.post("/api/integration/ai/callback")
def reserved_ai_callback():
    payload = request.get_json(silent=True) or {}
    return jsonify(handle_ai_callback(payload))
