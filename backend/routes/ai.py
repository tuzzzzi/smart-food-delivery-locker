import random
import string
import time

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Box, Package, Task
from services.ai_gateway import RUNTIME_ERROR_STATUSES, RETRYABLE_STATUSES, request_box_detection
from services.audit_log import log_action
from services.locker_gateway import (
    bind_package_to_latest_deposit_command,
    get_latest_task_locker_command,
    mark_deposit_ai_result,
    serialize_locker_trace,
)
from services.ownership import (
    backfill_order_receiver_openid,
    backfill_task_receiver_openid,
    resolve_task_receiver_openid,
)
from services.platform_flow import (
    ORDER_STATUS_READY_FOR_PICKUP,
    emit_hardware_event,
    emit_platform_event,
    get_platform_order_by_task,
    get_task_exception_meta,
    mark_order_ready_for_pickup,
    mark_task_exception_pending,
    mark_task_exception_resolved,
)
from utils.remind_utils import enqueue_auto_reminds

ai_bp = Blueprint("ai", __name__)


def gen_id(prefix):
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def gen_pickup_code():
    return "".join(random.choices(string.digits, k=6))


def resolve_receiver_openid(task: Task) -> str:
    return resolve_task_receiver_openid(
        task,
        allow_phone_lookup=False,
        allow_demo_fallback=False,
    )


def _deposit_evidence(task: Task):
    command = get_latest_task_locker_command(getattr(task, "task_id", None), scene="deposit")
    return serialize_locker_trace(command, audience="courier") if command else None


def _detector_runtime_error(detection) -> bool:
    status = (detection.get("detectorStatus") or "").strip()
    return status in RUNTIME_ERROR_STATUSES


def _detector_retryable(detection) -> bool:
    status = (detection.get("detectorStatus") or "").strip()
    return bool(detection.get("retryable")) or status in RETRYABLE_STATUSES


@ai_bp.post("/api/ai/verify_box")
def ai_verify_box():
    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    if not task_id:
        return jsonify({"status": "error", "message": "Missing taskId"}), 400

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Invalid taskId"}), 400

    if task.status != "waiting_ai":
        current_app.logger.info(
            "ai.verify_box called taskId=%s status=%s willTriggerYolo=%s",
            task.task_id,
            task.status,
            False,
        )
        return jsonify({
            "status": "error",
            "message": f"Task status must be waiting_ai, got {task.status}",
        }), 409

    box = Box.query.get(task.box_no)
    if not box:
        return jsonify({"status": "error", "message": "Box not found"}), 500

    if task.package_id:
        pkg = Package.query.get(task.package_id)
        if pkg:
            current_app.logger.info(
                "ai.verify_box called taskId=%s status=%s willTriggerYolo=%s packageExists=%s",
                task.task_id,
                task.status,
                False,
                True,
            )
            order = get_platform_order_by_task(task)
            should_emit = bool(order and order.status != ORDER_STATUS_READY_FOR_PICKUP)
            order = mark_order_ready_for_pickup(task, pkg)
            if should_emit:
                db.session.commit()
                emit_platform_event("order_ready_for_pickup", order=order, task=task, pkg=pkg)
                emit_hardware_event("package_ready_for_pickup", order=order, task=task, pkg=pkg)
            return jsonify({
                "status": "success",
                "task": task.to_dict(),
                "package": pkg.to_dict(),
                "depositEvidence": _deposit_evidence(task),
            })

    task.ai_checked_at = int(time.time())
    current_app.logger.info(
        "ai.verify_box called taskId=%s status=%s willTriggerYolo=%s",
        task.task_id,
        task.status,
        True,
    )
    detection = request_box_detection(task, box)

    if _detector_retryable(detection):
        current_app.logger.info(
            "ai.verify_box retryable taskId=%s detectorStatus=%s",
            task.task_id,
            detection.get("detectorStatus") or "-",
        )
        db.session.commit()
        return jsonify({
            "status": "pending",
            "retryable": True,
            "detectorStatus": detection.get("detectorStatus") or "snapshot_pending",
            "message": detection.get("message") or "入柜照片正在生成，请稍后",
            "task": task.to_dict(),
            "depositEvidence": _deposit_evidence(task),
            "integration": detection,
        }), 202

    if _detector_runtime_error(detection):
        task.ai_message = detection.get("detail") or "AI 运行异常。"
        log_action(
            "task.ai_runtime_error",
            actor_type="system",
            actor_openid="ai_service",
            platform_order_id=task.platform_order_id,
            task_id=task.task_id,
            detail=task.ai_message,
        )
        db.session.commit()
        return jsonify({
            "status": "error",
            "message": task.ai_message,
            "task": task.to_dict(),
            "depositEvidence": _deposit_evidence(task),
            "integration": detection,
        }), 503

    if not detection.get("ok"):
        task.status = "ai_failed"
        task.ai_message = detection.get("detail") or "AI 未检测到包裹，检测未通过。"
        mark_task_exception_pending(task, task.ai_message)
        mark_deposit_ai_result(
            task.task_id,
            passed=False,
            detail=task.ai_message,
        )
        log_action(
            "task.ai_failed",
            actor_type="system",
            actor_openid="ai_service",
            platform_order_id=task.platform_order_id,
            task_id=task.task_id,
            detail=task.ai_message,
        )
        db.session.commit()
        return jsonify({
            "status": "error",
            "message": task.ai_message,
            "task": task.to_dict(),
            "depositEvidence": _deposit_evidence(task),
            "integration": detection,
        }), 200

    pickup_code = gen_pickup_code()
    package_id = gen_id("PKG")
    arrived_ts = int(time.time())
    receiver_openid = resolve_receiver_openid(task)
    order = get_platform_order_by_task(task)
    if receiver_openid:
        backfill_task_receiver_openid(task, receiver_openid)
        backfill_order_receiver_openid(order, receiver_openid)
    else:
        task.status = "ai_failed"
        task.ai_message = "Receiver user is not resolved. Please bind the phone number to a unique user before generating Package."
        mark_task_exception_pending(task, task.ai_message)
        mark_deposit_ai_result(
            task.task_id,
            passed=False,
            detail=task.ai_message,
        )
        log_action(
            "task.receiver_unresolved",
            actor_type="system",
            actor_openid="ai_service",
            platform_order_id=task.platform_order_id,
            task_id=task.task_id,
            detail=task.ai_message,
        )
        db.session.commit()
        return jsonify({
            "status": "error",
            "message": task.ai_message,
            "task": task.to_dict(),
            "depositEvidence": _deposit_evidence(task),
            "integration": detection,
        }), 409

    pkg = Package(
        package_id=package_id,
        task_id=task.task_id,
        box_no=box.box_no,
        receiver_openid=receiver_openid or None,
        receiver_phone=task.receiver_phone,
        merchant_name=task.merchant_name or "",
        pickup_code=pickup_code,
        status="pending",
        arrived_at=arrived_ts,
    )
    db.session.add(pkg)
    bind_package_to_latest_deposit_command(task.task_id, pkg.package_id)
    mark_deposit_ai_result(
        task.task_id,
        passed=True,
        detail="AI verified that an item was placed in the locker.",
        package_id=pkg.package_id,
    )

    task.status = "ai_passed"
    task.package_id = pkg.package_id
    task.ai_message = None
    if get_task_exception_meta(task)["hasActiveException"]:
        mark_task_exception_resolved(task, "异常已处理完成，AI 已确认本次重新投递成功。")

    box.status = "occupied"
    box.task_id = task.task_id

    if pkg.receiver_openid and pkg.arrived_at:
        enqueue_auto_reminds(pkg.receiver_openid, pkg.package_id, pkg.arrived_at)

    order = mark_order_ready_for_pickup(task, pkg)
    log_action(
        "task.ai_passed",
        actor_type="system",
        actor_openid="ai_service",
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        package_id=pkg.package_id,
        detail=f"AI 检测通过，任务转为待取件，箱体 {task.box_no}。",
    )
    log_action(
        "package.ready_for_pickup",
        actor_type="system",
        actor_openid="ai_service",
        platform_order_id=getattr(order, "platform_order_id", None),
        task_id=task.task_id,
        package_id=pkg.package_id,
        detail="系统已生成包裹并进入待取件阶段。",
    )
    db.session.commit()
    emit_platform_event("order_ready_for_pickup", order=order, task=task, pkg=pkg)
    emit_hardware_event("package_ready_for_pickup", order=order, task=task, pkg=pkg)

    return jsonify({
        "status": "success",
        "task": task.to_dict(),
        "package": pkg.to_dict(),
        "depositEvidence": _deposit_evidence(task),
        "integration": detection,
    })
