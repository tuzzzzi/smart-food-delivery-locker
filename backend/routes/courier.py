import random
import time

from flask import Blueprint, current_app, g, jsonify, request

from extensions import db
from models import Box, Courier, Package, Task
from routes.guards import courier_required
from services.audit_log import log_action
from services.locker_gateway import (
    get_box_locker_status,
    get_latest_task_locker_command,
    request_open_door,
    serialize_locker_trace,
)
from services.ownership import PHONE_MATCHED, match_user_by_phone, phone_match_http_code
from services.platform_flow import (
    emit_platform_event,
    get_order_status_view,
    get_platform_order_by_task,
    get_task_exception_meta,
    get_task_stage,
    mark_task_exception_pending,
    reset_task_for_retry,
    rollback_task_to_pending_dispatch,
)

courier_bp = Blueprint("courier", __name__)
WORK_STATUS_OPTIONS = {
    "绌洪棽",
    "閰嶉€佷腑",
    "鏆傚仠鎺ュ崟",
    "空闲",
    "配送中",
    "暂停接单",
}


def gen_id(prefix):
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def _current_courier_openid():
    return (getattr(g, "current_openid", "") or "").strip()


def _current_courier():
    return getattr(g, "current_courier", None)


def _serialize_courier_profile(courier: Courier):
    courier_name = (courier.courier_name or "").strip() if courier else ""
    nickname = (courier.nickname or "").strip() if courier else ""
    return {
        "openid": courier.openid if courier else "",
        "courierName": courier_name,
        "courier_name": courier_name,
        "nickname": nickname,
        "username": nickname or courier_name,
        "phoneNumber": courier.phone_number if courier else "",
        "phone_number": courier.phone_number if courier else "",
        "email": "",
        "status": courier.status if courier else "",
        "workStatus": courier.work_status if courier else "",
        "work_status": courier.work_status if courier else "",
        "applyStatus": courier.apply_status if courier else "none",
        "apply_status": courier.apply_status if courier else "none",
        "reviewNote": courier.review_note if courier else "",
        "review_note": courier.review_note if courier else "",
        "reviewedAt": courier.reviewed_at.isoformat() if courier and courier.reviewed_at else None,
        "reviewed_at": courier.reviewed_at.isoformat() if courier and courier.reviewed_at else None,
    }


def _forbidden_task(task: Task):
    current_openid = _current_courier_openid()
    if not task or not current_openid:
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    if (task.courier_openid or "").strip() != current_openid:
        return jsonify({"status": "error", "message": "Forbidden task access"}), 403

    return None


def reserve_empty_box():
    box = Box.query.filter_by(status="empty").order_by(Box.box_no.asc()).first()
    if not box:
        return None
    box.status = "reserved"
    db.session.commit()
    return box


def _serialize_courier_task(task: Task):
    order = get_platform_order_by_task(task)
    task_stage = get_task_stage(task)
    order_status = get_order_status_view(order, task) if order else None
    exception_meta = get_task_exception_meta(task)

    return {
        "taskId": task.task_id,
        "source": "platform_order" if task.platform_order_id else "manual",
        "platformOrderId": task.platform_order_id,
        "verifyToken": order.qr_token if order and order.qr_token else "",
        "boxNo": task.box_no,
        "status": task.status,
        "receiverPhone": task.receiver_phone,
        "merchantName": task.merchant_name,
        "taskStage": task_stage,
        "platformOrderStatus": order_status,
        "aiMessage": task.ai_message,
        "exceptionStatus": exception_meta["status"],
        "exceptionStatusLabel": exception_meta["label"],
        "exceptionNote": exception_meta["note"],
        "hasActiveException": exception_meta["hasActiveException"],
        "exceptionResolved": exception_meta["resolved"],
    }


def _task_deposit_evidence(task: Task):
    if not task:
        return None

    command = get_latest_task_locker_command(task.task_id, scene="deposit")
    return serialize_locker_trace(command, audience="courier") if command else None


def _courier_pending_hardware_response(task: Task, door_result, *, code=202):
    current_app.logger.info(
        "courier.open_locker.pending task=%s box=%s command=%s http=%s",
        getattr(task, "task_id", "-"),
        getattr(task, "box_no", "-"),
        (door_result or {}).get("commandId") or "-",
        code,
    )
    return jsonify({
        "status": "success",
        "message": "Locker command created. Waiting for ESP32 polling, door-open and door-close callbacks before AI verification can start.",
        "pendingHardware": True,
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task and task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
        "integration": {"locker": door_result},
    }), code


def _courier_locker_conflict_response(task: Task, door_result):
    return jsonify({
        "status": "error",
        "message": door_result.get("note") or "Another locker command is still in progress for this box.",
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task and task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
        "integration": {"locker": door_result},
    }), 409


@courier_bp.get("/api/courier/active_task")
@courier_required
def active_task():
    courier_openid = _current_courier_openid()
    if not courier_openid:
        return jsonify({"status": "error", "message": "Missing courier identity"}), 401

    active_status = ["assigned", "reassigned", "waiting_ai", "ai_failed"]
    task = (
        Task.query
        .filter(Task.courier_openid == courier_openid, Task.status.in_(active_status))
        .order_by(Task.updated_at.desc())
        .first()
    )

    if not task:
        return jsonify({"status": "success", "task": None})

    return jsonify({
        "status": "success",
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
    })


def _courier_profile_response(courier: Courier):
    if not courier:
        return jsonify({"status": "error", "message": "Courier not found"}), 404
    return jsonify({"status": "success", "courier": _serialize_courier_profile(courier)})


@courier_bp.get("/api/courier/profile")
@courier_required
def courier_get_profile():
    return _courier_profile_response(_current_courier())


def _update_current_courier_profile():
    current_openid = _current_courier_openid()
    courier = Courier.query.filter_by(openid=current_openid).first()
    if not courier:
        return jsonify({"status": "error", "message": "Courier not found"}), 404
    data = request.get_json(silent=True) or {}
    current_app.logger.info("[courier.profile.update] openid=%s payload=%s", current_openid, data)
    courier_name = data.get("courierName", data.get("courier_name"))
    nickname = data.get("nickname")
    phone_number = data.get("phoneNumber", data.get("phone_number"))
    email = data.get("email")
    work_status = data.get("workStatus", data.get("work_status"))
    changed = False

    if courier_name is not None:
        courier_name = str(courier_name).strip()
        if courier_name != (courier.courier_name or ""):
            courier.courier_name = courier_name or None
            changed = True

    if nickname is not None:
        nickname = str(nickname).strip()
        if not nickname:
            nickname = (courier_name or courier.courier_name or "").strip()
        if nickname != (courier.nickname or ""):
            courier.nickname = nickname or None
            changed = True
    elif courier_name is not None and courier_name and not (courier.nickname or "").strip():
        courier.nickname = courier_name
        changed = True

    if phone_number is not None:
        phone_number = str(phone_number).strip()
        if phone_number and (not phone_number.isdigit() or len(phone_number) != 11):
            return jsonify({"status": "error", "message": "Phone number must be 11 digits"}), 400
        if phone_number != (courier.phone_number or ""):
            if phone_number:
                other = Courier.query.filter(Courier.phone_number == phone_number, Courier.openid != courier.openid).first()
                if other:
                    return jsonify({"status": "error", "message": "Phone number already used by another courier"}), 409
            courier.phone_number = phone_number or None
            changed = True

    if email is not None and hasattr(courier, "email"):
        email = str(email).strip()
        if email != (courier.email or ""):
            courier.email = email or None
            changed = True

    if work_status is not None:
        work_status = str(work_status).strip() or "绌洪棽"
        if work_status not in WORK_STATUS_OPTIONS:
            return jsonify({"status": "error", "message": "Invalid work status"}), 400
        if work_status != (courier.work_status or "绌洪棽"):
            courier.work_status = work_status
            changed = True

    db.session.commit()
    saved_courier = Courier.query.filter_by(openid=current_openid).first()
    current_app.logger.info(
        "[courier.profile.update] committed openid=%s courier=%s",
        current_openid,
        _serialize_courier_profile(saved_courier) if saved_courier else None,
    )

    return jsonify({"status": "success", "courier": _serialize_courier_profile(saved_courier)})


@courier_bp.post("/api/courier/profile")
@courier_bp.post("/api/courier/profile/update")
@courier_required
def courier_update_profile():
    return _update_current_courier_profile()


@courier_bp.get("/api/courier/tasks_overview")
@courier_required
def courier_tasks_overview():
    courier_openid = _current_courier_openid()
    if not courier_openid:
        return jsonify({"status": "error", "message": "Missing courier identity"}), 401

    tasks = (
        Task.query
        .filter(Task.courier_openid == courier_openid)
        .order_by(Task.updated_at.desc())
        .limit(30)
        .all()
    )

    result = {
        "pending": [],
        "inProgress": [],
        "completed": [],
        "closed": [],
    }

    for task in tasks:
        item = _serialize_courier_task(task)
        stage_key = ((item.get("taskStage") or {}).get("key") or "").strip()
        if stage_key == "pending":
            result["pending"].append(item)
        elif stage_key == "in_progress":
            result["inProgress"].append(item)
        elif stage_key == "completed":
            result["completed"].append(item)
        else:
            result["closed"].append(item)

    return jsonify({"status": "success", **result})


@courier_bp.post("/api/courier/create_task")
@courier_required
def courier_create_task():
    data = request.get_json(silent=True) or {}
    courier_openid = _current_courier_openid()
    receiver_phone = (data.get("receiverPhone") or "").strip()
    merchant_name = data.get("merchantName", "")

    if not courier_openid:
        return jsonify({"status": "error", "message": "Missing courier identity"}), 401

    phone_match = match_user_by_phone(receiver_phone)
    if phone_match["status"] != PHONE_MATCHED:
        return jsonify({
            "status": "error",
            "message": phone_match["message"] or "Receiver phone must match exactly one registered user before a delivery task can be created.",
            "reasonCode": phone_match.get("reasonCode") or "",
            "matchStatus": phone_match["status"],
        }), phone_match_http_code(phone_match)

    box = reserve_empty_box()
    if not box:
        return jsonify({"status": "error", "message": "No empty boxes"}), 409

    task = Task(
        task_id=gen_id("TASK"),
        status="assigned",
        box_no=box.box_no,
        courier_openid=courier_openid,
        receiver_openid=(phone_match.get("openid") or "").strip() or None,
        receiver_phone=receiver_phone,
        merchant_name=merchant_name,
    )
    db.session.add(task)
    box.task_id = task.task_id
    db.session.commit()

    return jsonify({
        "status": "success",
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
    })


@courier_bp.get("/api/courier/task")
@courier_required
def courier_get_task():
    task_id = request.args.get("taskId")
    if not task_id:
        return jsonify({"status": "error", "message": "Missing taskId"}), 400

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Invalid taskId"}), 400

    forbidden = _forbidden_task(task)
    if forbidden:
        return forbidden

    return jsonify({
        "status": "success",
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
    })


@courier_bp.get("/api/courier/package")
@courier_required
def courier_get_package():
    package_id = (request.args.get("packageId") or "").strip()
    if not package_id:
        return jsonify({"status": "error", "message": "Missing packageId"}), 400

    pkg = Package.query.get(package_id)
    if not pkg:
        return jsonify({"status": "error", "message": "Package not found"}), 404

    task = Task.query.get(pkg.task_id) if pkg.task_id else None
    forbidden = _forbidden_task(task)
    if forbidden:
        return forbidden
    order = get_platform_order_by_task(task) if task else None

    return jsonify({
        "status": "success",
        "package": {
            **pkg.to_dict(),
            "verifyToken": order.qr_token if order and order.qr_token else "",
        },
        "hardware": get_box_locker_status(pkg.box_no, event_limit=8) if pkg.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
        "task": _serialize_courier_task(task) if task else None,
    })


@courier_bp.post("/api/courier/open_locker_for_task")
@courier_bp.post("/api/courier/confirm_put_in_box")
@courier_required
def confirm_put_in_box():
    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    if not task_id:
        return jsonify({"status": "error", "message": "Missing taskId"}), 400

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Invalid taskId"}), 400

    forbidden = _forbidden_task(task)
    if forbidden:
        return forbidden

    if task.status == "waiting_ai":
        return jsonify({
            "status": "success",
            "message": "Locker deposit loop already completed. Waiting for AI verification.",
            "pendingHardware": False,
            "task": _serialize_courier_task(task),
            "hardware": get_box_locker_status(task.box_no, event_limit=8) if task.box_no else None,
            "depositEvidence": _task_deposit_evidence(task),
        })

    allowed = ["assigned", "reassigned", "ai_failed"]
    if task.status not in allowed:
        return jsonify({
            "status": "error",
            "message": f"Task not confirmable now: {task.status}",
            "taskStatus": task.status,
        }), 409

    box = Box.query.get(task.box_no)
    if not box:
        return jsonify({"status": "error", "message": "Box not found"}), 500

    if box.status == "occupied":
        return jsonify({
            "status": "error",
            "message": f"Box already occupied: {box.box_no}",
            "boxStatus": box.status,
            "boxNo": box.box_no,
        }), 409

    if box.status != "reserved":
        box.status = "reserved"
    box.task_id = task.task_id

    door_result = request_open_door(
        task.box_no,
        operator_type="courier",
        operator_id=task.courier_openid,
        reason="courier_put_in",
        task_id=task.task_id,
        package_id=None,
    )
    db.session.commit()

    task = Task.query.get(task.task_id)

    if door_result.get("conflict"):
        if door_result.get("reusedExistingCommand") and door_result.get("pendingHardware"):
            return _courier_pending_hardware_response(task, door_result)
        return _courier_locker_conflict_response(task, door_result)

    if door_result.get("pendingHardware"):
        return _courier_pending_hardware_response(task, door_result)

    if not door_result.get("accepted"):
        return jsonify({
            "status": "error",
            "message": "Locker controller unavailable",
            "task": _serialize_courier_task(task),
            "hardware": get_box_locker_status(task.box_no, event_limit=8) if task and task.box_no else None,
            "depositEvidence": _task_deposit_evidence(task),
            "integration": {"locker": door_result},
        }), 503

    return jsonify({
        "status": "success",
        "message": "Locker deposit loop completed. Waiting for AI verification.",
        "pendingHardware": False,
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task and task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
        "integration": {"locker": door_result},
    })


@courier_bp.post("/api/courier/cancel_task")
@courier_required
def cancel_task():
    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    if not task_id:
        return jsonify({"status": "error", "message": "Missing taskId"}), 400

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Task not found"}), 404

    forbidden = _forbidden_task(task)
    if forbidden:
        return forbidden

    if task.status not in ["assigned", "reassigned", "waiting_ai", "ai_failed"]:
        return jsonify({"status": "error", "message": f"Task cannot be submitted for admin handling: {task.status}"}), 409

    detail = "配送员已上报异常给管理员处理，等待管理员完成处置"
    mark_task_exception_pending(task, detail)

    log_action(
        "task.submitted_to_admin",
        actor_type="courier",
        actor_openid=task.courier_openid,
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        detail=detail,
    )
    db.session.commit()
    return jsonify({
        "status": "success",
        "message": "已提交管理员处理，当前异常将等待管理员完成处置",
        "task": _serialize_courier_task(task),
        "hardware": get_box_locker_status(task.box_no, event_limit=8) if task and task.box_no else None,
        "depositEvidence": _task_deposit_evidence(task),
    })


@courier_bp.post("/api/courier/reset_task_status")
@courier_required
def reset_task_status():
    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    if not task_id:
        return jsonify({"status": "error", "message": "Missing taskId"}), 400

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Task not found"}), 404

    forbidden = _forbidden_task(task)
    if forbidden:
        return forbidden

    if task.status not in ["waiting_ai", "ai_failed"]:
        return jsonify({"status": "error", "message": "Task status cannot be reset"}), 400

    try:
        order, _ = reset_task_for_retry(task)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409

    log_action(
        "task.reset_for_retry",
        actor_type="courier",
        actor_openid=task.courier_openid,
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        detail="配送员将任务重置为待重新投递",
    )
    db.session.commit()
    emit_platform_event("order_redispatched", order=order, task=task)
    return jsonify({"status": "success", "message": "任务状态已重置为待重新投递"}), 200
