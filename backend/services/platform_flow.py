import random
import time
from datetime import datetime

from extensions import db
from models import Box, Courier, Package, PlatformOrder, Task, User
from services.ownership import resolve_order_receiver_openid as _resolve_order_receiver_openid

ORDER_STATUS_PENDING_DISPATCH = "pending_dispatch"
ORDER_STATUS_DISPATCHED = "dispatched"
ORDER_STATUS_WAITING_AI = "waiting_ai"
ORDER_STATUS_READY_FOR_PICKUP = "ready_for_pickup"
ORDER_STATUS_COMPLETED = "completed"
ORDER_STATUS_CANCELLED = "cancelled"
ORDER_STATUS_EXCEPTION = "exception"

TASK_STAGE_PENDING = "pending"
TASK_STAGE_IN_PROGRESS = "in_progress"
TASK_STAGE_COMPLETED = "completed"
TASK_STAGE_CLOSED = "closed"

TASK_EXCEPTION_NONE = "none"
TASK_EXCEPTION_PENDING = "pending"
TASK_EXCEPTION_RESOLVED = "resolved"
TASK_EXCEPTION_STATUSES = {
    TASK_EXCEPTION_NONE,
    TASK_EXCEPTION_PENDING,
    TASK_EXCEPTION_RESOLVED,
}

TERMINAL_ORDER_STATUSES = {
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_EXCEPTION,
}


def _dt_from_ts(ts):
    if not ts:
        return None
    try:
        return datetime.utcfromtimestamp(int(ts))
    except Exception:
        return None


def _trim_note(note: str, default_text: str = ""):
    text = (note or "").strip()
    if not text:
        text = default_text
    return text[:255] if text else None


def _normalize_task_exception_status(value: str):
    status = (value or TASK_EXCEPTION_NONE).strip().lower()
    return status if status in TASK_EXCEPTION_STATUSES else TASK_EXCEPTION_NONE


def get_task_exception_meta(task: Task):
    if not task:
        return {
            "status": TASK_EXCEPTION_NONE,
            "label": "",
            "note": "",
            "hasActiveException": False,
            "resolved": False,
        }

    status = _normalize_task_exception_status(getattr(task, "exception_status", None))
    note = _trim_note(getattr(task, "exception_note", None) or getattr(task, "ai_message", None))

    if status == TASK_EXCEPTION_NONE and getattr(task, "status", "") == "ai_failed":
        status = TASK_EXCEPTION_PENDING
        note = note or _trim_note(getattr(task, "ai_message", None), "系统确认未通过，等待重新投递或人工处理")

    label_map = {
        TASK_EXCEPTION_NONE: "",
        TASK_EXCEPTION_PENDING: "异常处理中",
        TASK_EXCEPTION_RESOLVED: "异常已处理完成",
    }
    return {
        "status": status,
        "label": label_map.get(status, ""),
        "note": note or "",
        "hasActiveException": status == TASK_EXCEPTION_PENDING,
        "resolved": status == TASK_EXCEPTION_RESOLVED,
    }


def mark_task_exception_pending(task: Task, note: str = ""):
    if not task:
        return None
    task.exception_status = TASK_EXCEPTION_PENDING
    task.exception_note = _trim_note(note) or task.exception_note
    return task


def mark_task_exception_resolved(task: Task, note: str = "", *, force: bool = False):
    if not task:
        return None
    current_status = _normalize_task_exception_status(getattr(task, "exception_status", None))
    if current_status == TASK_EXCEPTION_NONE and not force and getattr(task, "status", "") != "ai_failed":
        return task
    task.exception_status = TASK_EXCEPTION_RESOLVED
    task.exception_note = _trim_note(note) or task.exception_note
    return task


def _reset_non_terminal_fields(order: PlatformOrder, clear_completed: bool = True):
    if not order:
        return None
    order.closed_at = None
    order.status_note = None
    if clear_completed:
        order.completed_at = None
    return order


def get_platform_order_by_task(task: Task):
    if not task or not getattr(task, "platform_order_id", None):
        return None
    return PlatformOrder.query.get(task.platform_order_id)


def get_latest_task_for_order(platform_order_id: str):
    if not platform_order_id:
        return None
    return (
        Task.query
        .filter_by(platform_order_id=platform_order_id)
        .order_by(Task.created_at.desc())
        .first()
    )


def get_platform_order_by_package(pkg: Package):
    if not pkg or not pkg.task_id:
        return None
    task = Task.query.get(pkg.task_id)
    if not task:
        return None
    return get_platform_order_by_task(task)


def resolve_order_receiver_openid(order: PlatformOrder):
    return _resolve_order_receiver_openid(
        order,
        allow_phone_lookup=True,
        allow_demo_fallback=False,
    )


def _gen_id(prefix):
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def get_task_stage(task: Task):
    if not task:
        return None

    if task.status in ("assigned", "reassigned"):
        return {
            "key": TASK_STAGE_PENDING,
            "label": "待执行",
            "badgeClass": "b-yellow",
            "description": "配送员已分配，等待开始投递",
        }

    if task.status in ("waiting_ai", "ai_failed"):
        desc = "已完成入柜，等待系统确认"
        if task.status == "ai_failed":
            desc = "系统确认未通过，等待重新投递或异常收口"
        return {
            "key": TASK_STAGE_IN_PROGRESS,
            "label": "执行中",
            "badgeClass": "b-blue",
            "description": desc,
        }

    if task.status == "ai_passed":
        return {
            "key": TASK_STAGE_COMPLETED,
            "label": "已完成",
            "badgeClass": "b-green",
            "description": "任务已完成入柜，进入用户取件阶段",
        }

    if task.status == "cancelled":
        return {
            "key": TASK_STAGE_CLOSED,
            "label": "已关闭",
            "badgeClass": "b-red",
            "description": "任务已取消或异常关闭",
        }

    return {
        "key": task.status,
        "label": task.status,
        "badgeClass": "",
        "description": "任务状态保持原样展示",
    }


def get_order_status_view(order: PlatformOrder, task: Task = None, pkg: Package = None):
    if not order:
        return {
            "key": "",
            "label": "-",
            "rawLabel": "-",
            "badgeClass": "",
            "description": "",
        }

    if order.status == ORDER_STATUS_PENDING_DISPATCH:
        if not resolve_order_receiver_openid(order):
            return {
                "key": order.status,
                "label": "待匹配用户",
                "rawLabel": "待派单",
                "badgeClass": "b-red",
                "description": (order.status_note or "").strip() or "当前手机号尚未匹配到唯一用户，暂不能继续派单",
            }
        return {
            "key": order.status,
            "label": "待派单",
            "rawLabel": "待派单",
            "badgeClass": "b-yellow",
            "description": "订单已进入系统，等待管理员选择配送员",
        }

    if order.status == ORDER_STATUS_DISPATCHED:
        if task and task.status in ("assigned", "reassigned"):
            return {
                "key": order.status,
                "label": "配送中",
                "rawLabel": "已派单",
                "badgeClass": "b-blue",
                "description": "配送员已接到任务，正在执行投递",
            }
        return {
            "key": order.status,
            "label": "已派单",
            "rawLabel": "已派单",
            "badgeClass": "b-blue",
            "description": "订单已分配配送员，等待继续执行",
        }

    if order.status == ORDER_STATUS_WAITING_AI:
        if task and task.status == "ai_failed":
            return {
                "key": order.status,
                "label": "确认未通过",
                "rawLabel": "待确认",
                "badgeClass": "b-red",
                "description": "包裹已入柜，但系统确认未通过，等待重试或异常收口",
            }
        return {
            "key": order.status,
            "label": "等待确认",
            "rawLabel": "待确认",
            "badgeClass": "b-yellow",
            "description": "包裹已完成入柜，等待系统确认通过",
        }

    if order.status == ORDER_STATUS_READY_FOR_PICKUP:
        return {
            "key": order.status,
            "label": "待取件",
            "rawLabel": "待取件",
            "badgeClass": "b-green",
            "description": "入柜已确认，等待用户取件",
        }

    if order.status == ORDER_STATUS_COMPLETED:
        return {
            "key": order.status,
            "label": "已完成",
            "rawLabel": "已完成",
            "badgeClass": "b-green",
            "description": "用户已完成取件，订单流程闭环",
        }

    if order.status == ORDER_STATUS_CANCELLED:
        return {
            "key": order.status,
            "label": "已取消",
            "rawLabel": "已取消",
            "badgeClass": "b-red",
            "description": "订单已取消，不再继续投递流程",
        }

    if order.status == ORDER_STATUS_EXCEPTION:
        return {
            "key": order.status,
            "label": "异常关闭",
            "rawLabel": "异常",
            "badgeClass": "b-red",
            "description": "订单已按异常流程收口，等待人工说明",
        }

    return {
        "key": order.status,
        "label": order.status,
        "rawLabel": order.status,
        "badgeClass": "",
        "description": "订单状态保持原样展示",
    }


def get_order_timeline(order: PlatformOrder, task: Task = None, pkg: Package = None):
    created_at = order.created_at or order.pushed_at
    dispatch_done = bool(order.dispatched_at or task)
    dispatch_current = order.status == ORDER_STATUS_PENDING_DISPATCH

    task_stage = get_task_stage(task)
    delivery_done = bool(task and task.status not in ("assigned", "reassigned"))
    delivery_current = bool(task and task_stage and task_stage["key"] == TASK_STAGE_PENDING)

    locker_done = bool((task and task.put_in_at) or order.status in (ORDER_STATUS_READY_FOR_PICKUP, ORDER_STATUS_COMPLETED))
    locker_current = bool(order.status == ORDER_STATUS_WAITING_AI)
    locker_time = None
    if pkg and pkg.arrived_at:
        locker_time = _dt_from_ts(pkg.arrived_at)
    elif task and task.put_in_at:
        locker_time = _dt_from_ts(task.put_in_at)

    pickup_done = bool(order.status == ORDER_STATUS_COMPLETED)
    pickup_current = bool(order.status == ORDER_STATUS_READY_FOR_PICKUP)
    pickup_time = order.completed_at or (pkg and _dt_from_ts(pkg.picked_at))

    return [
        {
            "label": "平台接单",
            "stateClass": "step-done",
            "hint": "平台订单已进入系统",
            "time": created_at,
        },
        {
            "label": "管理员派单",
            "stateClass": "step-current" if dispatch_current else ("step-done" if dispatch_done else "step-pending"),
            "hint": "选择已审核通过的配送员生成任务",
            "time": order.dispatched_at,
        },
        {
            "label": "配送执行",
            "stateClass": "step-current" if delivery_current else ("step-done" if delivery_done else "step-pending"),
            "hint": task_stage["description"] if task_stage else "等待生成配送任务",
            "time": task.updated_at if task else None,
        },
        {
            "label": "入柜确认",
            "stateClass": "step-current" if locker_current else ("step-done" if locker_done else "step-pending"),
            "hint": "完成入柜并确认无误后进入待取件",
            "time": locker_time,
        },
        {
            "label": "用户取件",
            "stateClass": "step-current" if pickup_current else ("step-done" if pickup_done else "step-pending"),
            "hint": "用户完成取件后订单闭环",
            "time": pickup_time,
        },
    ]


def get_order_action_state(order: PlatformOrder, task: Task = None, pkg: Package = None):
    if not order:
        return {
            "canDispatch": False,
            "canCancel": False,
            "canException": False,
            "closeHint": "订单不存在",
        }

    if order.status == ORDER_STATUS_COMPLETED:
        return {
            "canDispatch": False,
            "canCancel": False,
            "canException": False,
            "closeHint": "订单已完成，无需再做取消或异常收口",
        }

    if order.status in (ORDER_STATUS_CANCELLED, ORDER_STATUS_EXCEPTION):
        return {
            "canDispatch": False,
            "canCancel": False,
            "canException": False,
            "closeHint": "订单已进入终态",
        }

    if pkg and pkg.status != "picked":
        return {
            "canDispatch": False,
            "canCancel": False,
            "canException": False,
            "closeHint": "订单已生成待取包裹，需按原有包裹流程继续处理",
        }

    resolved_openid = resolve_order_receiver_openid(order)
    if not resolved_openid:
        return {
            "canDispatch": False,
            "canCancel": order.status in (ORDER_STATUS_PENDING_DISPATCH, ORDER_STATUS_DISPATCHED),
            "canException": order.status in (ORDER_STATUS_PENDING_DISPATCH, ORDER_STATUS_DISPATCHED, ORDER_STATUS_WAITING_AI),
            "closeHint": (order.status_note or "").strip() or "当前订单未匹配到唯一收件用户，需先完成用户绑号后再派单",
        }

    can_dispatch = order.status == ORDER_STATUS_PENDING_DISPATCH and (not task or task.status == "cancelled")
    can_cancel = order.status in (ORDER_STATUS_PENDING_DISPATCH, ORDER_STATUS_DISPATCHED)
    can_exception = order.status in (ORDER_STATUS_PENDING_DISPATCH, ORDER_STATUS_DISPATCHED, ORDER_STATUS_WAITING_AI)

    return {
        "canDispatch": can_dispatch,
        "canCancel": can_cancel,
        "canException": can_exception,
        "closeHint": "",
    }


def dispatch_order_to_task(order: PlatformOrder, courier_openid: str):
    if not order:
        raise ValueError("Platform order not found")
    if not courier_openid:
        raise ValueError("Missing courierOpenid")

    courier = Courier.query.filter_by(openid=courier_openid, apply_status="approved").first()
    if not courier:
        raise ValueError("Courier must be approved before dispatch")

    if order.status in (ORDER_STATUS_READY_FOR_PICKUP, ORDER_STATUS_COMPLETED, ORDER_STATUS_CANCELLED, ORDER_STATUS_EXCEPTION):
        raise ValueError(f"Order cannot be dispatched in current status: {order.status}")

    latest_task = get_latest_task_for_order(order.platform_order_id)
    if latest_task and latest_task.status != "cancelled":
        return latest_task, False

    box = Box.query.filter_by(status="empty").order_by(Box.box_no.asc()).first()
    if not box:
        raise RuntimeError("No empty boxes")

    resolved_openid = resolve_order_receiver_openid(order)
    if not resolved_openid:
        raise ValueError("Receiver user is not resolved for this order")

    box.status = "reserved"
    task = Task(
        task_id=_gen_id("TASK"),
        status="assigned",
        platform_order_id=order.platform_order_id,
        box_no=box.box_no,
        courier_openid=courier_openid,
        receiver_openid=resolved_openid or None,
        receiver_phone=order.receiver_phone,
        merchant_name=order.merchant_name or "",
    )
    db.session.add(task)

    box.task_id = task.task_id
    if resolved_openid:
        order.receiver_openid = resolved_openid
    mark_order_dispatched(order, courier_openid=courier_openid)

    return task, True


def close_order(order: PlatformOrder, target_status: str, note: str = ""):
    if not order:
        raise ValueError("Platform order not found")
    if target_status not in (ORDER_STATUS_CANCELLED, ORDER_STATUS_EXCEPTION):
        raise ValueError("Unsupported close status")
    if order.status in TERMINAL_ORDER_STATUSES:
        raise ValueError("Order is already closed")

    task = get_latest_task_for_order(order.platform_order_id)
    pkg = Package.query.get(task.package_id) if task and task.package_id else None

    if pkg and pkg.status != "picked":
        raise ValueError("Order already has a package waiting for pickup and cannot be closed here")

    if task and task.status not in ("cancelled", "ai_passed"):
        task.status = "cancelled"
        task.ai_message = _trim_note(
            note,
            "平台订单已取消" if target_status == ORDER_STATUS_CANCELLED else "平台订单已按异常关闭",
        )
        mark_task_exception_resolved(
            task,
            note or ("管理员已取消任务" if target_status == ORDER_STATUS_CANCELLED else "管理员已完成异常收口"),
            force=True,
        )

    if task:
        box = Box.query.get(task.box_no)
        if box and box.task_id == task.task_id and box.status != "occupied":
            box.status = "empty"
            box.task_id = None

    order.status = target_status
    order.closed_at = datetime.utcnow()
    order.status_note = _trim_note(
        note,
        "平台订单已取消" if target_status == ORDER_STATUS_CANCELLED else "平台订单异常关闭",
    )
    order.completed_at = None

    return order, task, pkg


def reset_task_for_retry(task: Task, note: str = ""):
    if not task:
        raise ValueError("Task not found")
    if task.status not in ("waiting_ai", "ai_failed"):
        raise ValueError(f"Task cannot be reset for retry: {task.status}")

    box = Box.query.get(task.box_no)
    if not box:
        raise ValueError("Box not found")
    if box.status == "occupied" and box.task_id != task.task_id:
        raise ValueError("Box already occupied by another task")

    box.status = "reserved"
    box.task_id = task.task_id
    task.status = "reassigned"
    task.ai_message = _trim_note(note)
    mark_task_exception_resolved(task, note or "配送员已选择重新投递，原异常已处理完成", force=True)

    order = get_platform_order_by_task(task)
    order = mark_order_dispatched(order, courier_openid=task.courier_openid)
    return order, box


def rollback_task_to_pending_dispatch(task: Task, note: str = ""):
    if not task:
        raise ValueError("Task not found")
    if task.status not in ("assigned", "reassigned", "waiting_ai", "ai_failed"):
        raise ValueError(f"Task cannot be rolled back: {task.status}")
    previous_status = task.status

    if task.package_id:
        pkg = Package.query.get(task.package_id)
        if pkg and pkg.status != "picked":
            raise ValueError("Task already has a package waiting for pickup")

    box = Box.query.get(task.box_no)
    if box and box.task_id == task.task_id and box.status != "occupied":
        box.status = "empty"
        box.task_id = None

    task.status = "cancelled"
    task.ai_message = _trim_note(note)
    if _normalize_task_exception_status(getattr(task, "exception_status", None)) == TASK_EXCEPTION_PENDING or previous_status in {"waiting_ai", "ai_failed"}:
        mark_task_exception_resolved(task, note or "当前异常已处理完成，任务已回退待派单", force=True)

    order = mark_order_pending_dispatch(task)
    return order, box


def mark_order_dispatched(order: PlatformOrder, courier_openid: str = None):
    if not order:
        return None

    order.status = ORDER_STATUS_DISPATCHED
    _reset_non_terminal_fields(order)
    if courier_openid:
        order.courier_openid = courier_openid
    if not order.dispatched_at:
        order.dispatched_at = datetime.utcnow()
    return order


def mark_order_waiting_ai(task: Task):
    order = get_platform_order_by_task(task)
    if not order:
        return None

    order.status = ORDER_STATUS_WAITING_AI
    _reset_non_terminal_fields(order)
    if task.courier_openid:
        order.courier_openid = task.courier_openid
    if not order.dispatched_at:
        order.dispatched_at = datetime.utcnow()
    return order


def mark_order_pending_dispatch(task: Task):
    order = get_platform_order_by_task(task)
    if not order:
        return None

    order.status = ORDER_STATUS_PENDING_DISPATCH
    _reset_non_terminal_fields(order)
    if order.courier_openid == task.courier_openid:
        order.courier_openid = None
    return order


def mark_order_ready_for_pickup(task: Task, pkg: Package):
    order = get_platform_order_by_task(task)
    if not order:
        return None

    order.status = ORDER_STATUS_READY_FOR_PICKUP
    _reset_non_terminal_fields(order)
    if pkg and pkg.receiver_openid and not order.receiver_openid:
        order.receiver_openid = pkg.receiver_openid
    return order


def mark_order_completed_for_package(pkg: Package):
    order = get_platform_order_by_package(pkg)
    if not order:
        return None

    order.status = ORDER_STATUS_COMPLETED
    order.closed_at = None
    order.status_note = None
    if pkg.receiver_openid and not order.receiver_openid:
        order.receiver_openid = pkg.receiver_openid
    order.completed_at = datetime.utcnow()
    return order


def emit_platform_event(event_name: str, order: PlatformOrder = None, task: Task = None, pkg: Package = None):
    """
    Placeholder for future outbound platform callbacks/webhooks.
    """
    from services.platform_gateway import emit_platform_event as _emit_platform_event

    return _emit_platform_event(event_name, order=order, task=task, pkg=pkg)


def emit_hardware_event(event_name: str, order: PlatformOrder = None, task: Task = None, pkg: Package = None):
    """
    Placeholder for future locker / controller integration.
    """
    from services.locker_gateway import emit_hardware_event as _emit_hardware_event

    return _emit_hardware_event(event_name, order=order, task=task, pkg=pkg)
