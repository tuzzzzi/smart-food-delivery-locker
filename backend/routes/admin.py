# routes/admin.py
import json
import random
import re
import time
from types import SimpleNamespace

from flask import Blueprint, g, redirect, render_template, request, url_for
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config import Config
from extensions import db
from models import User, Task, Box, Package, Courier, RemindEvent, RemindPolicy, PickupHistory, PlatformOrder, AdminLog, LockerCommand
from routes.guards import (
    authenticate_admin_request,
    authenticate_admin_token,
    clear_admin_session,
    get_request_token,
    resolve_admin_by_token,
)
from services.audit_log import log_action, serialize_log
from services.integration_runtime import get_integration_status_cards
from services.locker_gateway import (
    LOCKER_ALERT_EVENT_TYPES,
    LOCKER_DOOR_EVENT_TYPES,
    LOCKER_LOCK_EVENT_TYPES,
    get_box_locker_status,
    get_box_locker_status_map,
    get_locker_command,
    list_recent_locker_commands,
    list_recent_locker_events,
    serialize_locker_command,
)
from services.platform_flow import (
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_DISPATCHED,
    ORDER_STATUS_EXCEPTION,
    ORDER_STATUS_PENDING_DISPATCH,
    ORDER_STATUS_READY_FOR_PICKUP,
    ORDER_STATUS_WAITING_AI,
    close_order,
    dispatch_order_to_task,
    emit_hardware_event,
    emit_platform_event,
    get_latest_task_for_order,
    get_order_action_state,
    get_order_status_view,
    get_task_exception_meta,
    get_order_timeline,
    get_task_stage,
    mark_task_exception_pending,
    mark_task_exception_resolved,
    reset_task_for_retry,
    rollback_task_to_pending_dispatch,
)
from services.mock_identities import get_mock_identity, list_mock_identities
from services.order_qr import ensure_order_qr
from services.ownership import PHONE_INVALID, PHONE_MATCHED, PHONE_MISSING, match_user_by_phone

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
TASK_ACTIVE_STATUSES = {"assigned", "reassigned", "waiting_ai", "ai_failed"}


def _safe_next_url(value: str):
    value = (value or "").strip()
    if value.startswith("/admin"):
        return value
    return url_for("admin.dashboard")


def _current_admin_openid():
    admin_user = getattr(g, "current_admin", None)
    if admin_user and admin_user.openid:
        return admin_user.openid
    return "admin_console"


def _admin_login_redirect(message: str = ""):
    next_url = request.path
    return redirect(url_for("admin.login", next=next_url, message=message))


@admin_bp.before_request
def require_admin_access():
    if request.endpoint in {"admin.login", "admin.logout"}:
        return None

    user = authenticate_admin_request(persist_session=True)
    if user:
        return None

    token = get_request_token()
    if not token:
        return _admin_login_redirect("请先登录管理员账号")

    candidate = resolve_admin_by_token(token)
    if not candidate:
        return _admin_login_redirect("管理员身份校验失败，请重新登录")

    return _admin_login_redirect("请重新登录管理员后台")


@admin_bp.context_processor
def inject_admin_context():
    return {"current_admin": getattr(g, "current_admin", None)}


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(request.values.get("next"))
    existing = authenticate_admin_request(persist_session=True)
    if existing:
        return redirect(next_url)

    message = (request.values.get("message") or "").strip()
    token_value = ""

    if request.method == "POST":
        token_value = (request.form.get("token") or "").strip()
        admin_user = authenticate_admin_token(token_value, persist_session=True)
        if admin_user:
            return redirect(next_url)

        if not token_value:
            message = "请输入管理员凭证"
        else:
            candidate = resolve_admin_by_token(token_value)
            if not candidate:
                message = "token 无效，未找到对应管理员"
            else:
                message = "管理员登录失败，请重试"

    return render_template(
        "admin/login.html",
        next_url=next_url,
        token_value=token_value,
        message=message,
        mock_admin_identities=list_mock_identities(audience="admin_console") if Config.USE_MOCK else [],
    )


@admin_bp.get("/logout")
def logout():
    clear_admin_session()
    return redirect(url_for("admin.login", message="管理员已退出登录"))


def _approved_courier_context():
    couriers = (
        Courier.query
        .filter_by(apply_status="approved")
        .order_by(Courier.created_at.desc())
        .all()
    )
    openids = [c.openid for c in couriers if c.openid]
    user_map = _load_user_map(openids)
    courier_user_map = {}
    for courier in couriers:
        if not courier.openid:
            continue
        user = user_map.get(courier.openid)
        courier_user_map[courier.openid] = SimpleNamespace(
            openid=courier.openid,
            user_id=getattr(user, "user_id", "-"),
            username=_courier_display_name(courier, user),
            phone_number=(courier.phone_number or getattr(user, "phone_number", "") or ""),
            email=getattr(user, "email", ""),
        )
    return couriers, courier_user_map


def _build_platform_order_row(order: PlatformOrder):
    task = get_latest_task_for_order(order.platform_order_id)
    pkg = Package.query.get(task.package_id) if task and task.package_id else None
    status_view = get_order_status_view(order, task, pkg)
    task_stage = get_task_stage(task) if task else None
    action_state = get_order_action_state(order, task, pkg)
    current_box_no = (task.box_no if task and task.box_no else "") or (pkg.box_no if pkg and pkg.box_no else "") or None

    if order.status == ORDER_STATUS_PENDING_DISPATCH:
        attention_meta = _badge_meta("等待管理员派单", "b-yellow", "当前最关键动作是给订单选择配送员")
    elif task and task.status == "ai_failed":
        attention_meta = _badge_meta("需要人工处理", "b-red", "系统确认未通过，可重试任务、回退或异常关闭")
    elif order.status == ORDER_STATUS_WAITING_AI:
        attention_meta = _badge_meta("等待系统确认", "b-blue", "配送员已完成入柜，系统正在确认结果")
    elif order.status == ORDER_STATUS_READY_FOR_PICKUP:
        attention_meta = _badge_meta("等待用户取件", "b-blue", "包裹已入柜，等待用户开门取件")
    elif order.status == ORDER_STATUS_COMPLETED:
        attention_meta = _badge_meta("业务闭环完成", "b-green", "平台订单、包裹和箱体都已完成收口")
    elif order.status == ORDER_STATUS_CANCELLED:
        attention_meta = _badge_meta("已取消", "b-gray", "订单已取消，不再继续")
    else:
        attention_meta = _badge_meta("异常关闭", "b-red", "订单已按异常流程收口")

    return {
        "order": order,
        "task": task,
        "package": pkg,
        "status_view": status_view,
        "task_stage": task_stage,
        "task_status_meta": _task_status_meta(task.status) if task else None,
        "package_status_meta": _package_status_meta(pkg.status) if pkg else None,
        "dispatch_meta": _flag_meta(
            bool(order.courier_openid or task),
            "已派单",
            "未派单",
            true_hint="订单已分配配送员",
            false_hint="等待管理员派单",
        ),
        "task_created_meta": _flag_meta(
            bool(task),
            "任务已生成",
            "未生成任务",
            true_hint=f"任务号：{task.task_id}" if task else "",
            false_hint="派单成功后自动生成任务",
        ),
        "package_created_meta": _flag_meta(
            bool(pkg),
            "包裹已生成",
            "未生成包裹",
            true_hint=f"包裹号：{pkg.package_id}" if pkg else "",
            false_hint="系统确认通过后才会生成包裹",
        ),
        "pickup_meta": (
            _badge_meta("已完成取件", "b-green", "用户已完成取件")
            if pkg and pkg.status == "picked"
            else _badge_meta("待用户取件", "b-blue", "包裹已入柜，等待用户取件")
            if pkg and pkg.status == "pending"
            else _badge_meta("尚未进入取件阶段", "b-gray", "当前还没有进入用户取件环节")
        ),
        "attention_meta": attention_meta,
        "current_box_no": current_box_no,
        "timeline": get_order_timeline(order, task, pkg),
        "can_dispatch": action_state["canDispatch"],
        "can_cancel": action_state["canCancel"],
        "can_exception": action_state["canException"],
        "close_hint": action_state["closeHint"],
        "can_retry_task": bool(task and task.status in ("waiting_ai", "ai_failed") and not pkg),
        "can_rollback_task": bool(task and task.status in ("assigned", "reassigned", "waiting_ai", "ai_failed") and not pkg),
    }


def _build_task_row(task: Task, order_map: dict):
    order = order_map.get(task.platform_order_id) if task.platform_order_id else None
    pkg = Package.query.get(task.package_id) if task.package_id else None
    return {
        "task": task,
        "order": order,
        "package": pkg,
        "source_meta": _flag_meta(
            bool(task.platform_order_id),
            "平台订单任务",
            "手动兼容任务",
            true_badge_class="b-blue",
            false_badge_class="b-gray",
            true_hint="任务来自平台订单派单",
            false_hint="任务由配送员手动创建",
        ),
        "task_status_meta": _task_status_meta(task.status),
        "task_stage": get_task_stage(task),
        "package_status_meta": _package_status_meta(pkg.status) if pkg else None,
        "order_status_view": get_order_status_view(order, task, pkg) if order else None,
        "put_in_at_text": _format_unix_ts(task.put_in_at),
        "ai_checked_at_text": _format_unix_ts(task.ai_checked_at),
    }


def _load_user_map(openids):
    cleaned = sorted({(openid or "").strip() for openid in openids if (openid or "").strip()})
    if not cleaned:
        return {}
    users = User.query.filter(User.openid.in_(cleaned)).all()
    user_map = {user.openid: user for user in users}
    for openid in cleaned:
        if openid in user_map:
            continue
        identity = get_mock_identity(openid)
        if identity and (identity.get("user_role") or "").strip() == "courier":
            user_map[openid] = SimpleNamespace(
                openid=openid,
                user_id="-",
                username=identity.get("username") or identity.get("label") or openid,
                phone_number=identity.get("phone_number") or "",
                email=identity.get("email") or "",
            )
    return user_map


def _load_courier_map(openids):
    cleaned = sorted({(openid or "").strip() for openid in openids if (openid or "").strip()})
    if not cleaned:
        return {}
    couriers = Courier.query.filter(Courier.openid.in_(cleaned)).all()
    return {courier.openid: courier for courier in couriers}


def _courier_display_name(courier: Courier = None, user: User = None, openid: str = ""):
    for value in (
        courier.courier_name if courier else "",
        courier.nickname if courier else "",
        user.username if user else "",
        openid,
        courier.openid if courier else "",
    ):
        text = (value or "").strip()
        if text:
            return text
    return "-"


def _person_display_name(user: User = None, openid: str = ""):
    if user and user.username:
        return user.username
    return openid or "-"


def _phone_text(*values):
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return "-"


def _build_admin_exception_row(task: Task, order_map: dict, user_map: dict, courier_map: dict):
    order = order_map.get(task.platform_order_id) if task.platform_order_id else None
    pkg = Package.query.get(task.package_id) if task.package_id else None
    courier_user = user_map.get(task.courier_openid) if task.courier_openid else None
    receiver_openid = (
        task.receiver_openid
        or (order.receiver_openid if order and order.receiver_openid else "")
    )
    buyer_user = user_map.get(receiver_openid) if receiver_openid else None
    courier_profile = courier_map.get(task.courier_openid) if task.courier_openid else None
    exception_meta = get_task_exception_meta(task)
    current_note = (exception_meta.get("note") or "").strip()

    return {
        "task": task,
        "order": order,
        "package": pkg,
        "task_stage": get_task_stage(task),
        "task_status_meta": _task_status_meta(task.status),
        "order_status_view": get_order_status_view(order, task, pkg) if order else None,
        "exception_meta": exception_meta,
        "buyer_name": _person_display_name(buyer_user, receiver_openid),
        "buyer_phone": _phone_text(task.receiver_phone, order.receiver_phone if order else "", buyer_user.phone_number if buyer_user else ""),
        "courier_name": _courier_display_name(courier_profile, courier_user, task.courier_openid),
        "courier_phone": _phone_text(courier_profile.phone_number if courier_profile else "", courier_user.phone_number if courier_user else ""),
        "current_note": current_note or "配送员已提交管理员处理，等待管理员跟进",
        "repair_note": "管理员维修中",
        "contact_note": "待联系买家",
        "resolved_note": "已处理完成",
    }


def _latest_package_command(package_id: str, scene: str = None):
    if not package_id:
        return None

    query = LockerCommand.query.filter_by(package_id=package_id)
    if scene:
        query = query.filter_by(recording_scene=scene)
    return (
        query.order_by(LockerCommand.created_at.desc(), LockerCommand.command_id.desc())
        .first()
    )


def _build_package_row(pkg: Package, task_map: dict, order_map: dict):
    task = task_map.get(pkg.task_id)
    order = order_map.get(task.platform_order_id) if task and task.platform_order_id else None
    latest_command = _latest_package_command(pkg.package_id)
    deposit_command = _latest_package_command(pkg.package_id, scene="deposit")
    pickup_command = _latest_package_command(pkg.package_id, scene="pickup")
    return {
        "package": pkg,
        "task": task,
        "order": order,
        "status_meta": _package_status_meta(pkg.status),
        "task_stage": get_task_stage(task) if task else None,
        "order_status_view": get_order_status_view(order, task, pkg) if order else None,
        "arrived_at_text": _format_unix_ts(pkg.arrived_at),
        "picked_at_text": _format_unix_ts(pkg.picked_at),
        "latest_command": _decorate_locker_command_view(serialize_locker_command(latest_command)) if latest_command else None,
        "deposit_command": _decorate_locker_command_view(serialize_locker_command(deposit_command)) if deposit_command else None,
        "pickup_command": _decorate_locker_command_view(serialize_locker_command(pickup_command)) if pickup_command else None,
    }


def _build_box_row(box: Box, hardware, task: Task = None, pkg: Package = None, order: PlatformOrder = None):
    latest_command = _decorate_locker_command_view(((hardware or {}).get("latestCommand") or None))
    return {
        "box": box,
        "hardware": hardware,
        "task": task,
        "package": pkg,
        "order": order,
        "status_meta": _box_status_meta(box.status),
        "task_stage": get_task_stage(task) if task else None,
        "package_status_meta": _package_status_meta(pkg.status) if pkg else None,
        "latest_command": latest_command,
        "latest_alert": (hardware or {}).get("latestAlertEvent") if hardware else None,
    }


def _task_action_redirect(task: Task, back_to: str, notice: str, notice_type: str):
    if back_to == "detail" and task and task.platform_order_id:
        return redirect(url_for(
            "admin.platform_order_detail",
            platform_order_id=task.platform_order_id,
            notice=notice,
            notice_type=notice_type,
        ))

    return redirect(url_for(
        "admin.exceptions",
        notice=notice,
        notice_type=notice_type,
    ))


def _dispatch_redirect(platform_order_id: str, back_to: str, status: str, notice: str, notice_type: str):
    if back_to == "detail":
        return redirect(url_for(
            "admin.platform_order_detail",
            platform_order_id=platform_order_id,
            notice=notice,
            notice_type=notice_type,
        ))

    if back_to == "exceptions":
        return redirect(url_for(
            "admin.exceptions",
            notice=notice,
            notice_type=notice_type,
        ))

    return redirect(url_for(
        "admin.platform_orders",
        status=status or "all",
        notice=notice,
        notice_type=notice_type,
    ))


def _platform_orders_redirect(status: str, notice: str, notice_type: str):
    return redirect(url_for(
        "admin.platform_orders",
        status=status or "all",
        notice=notice,
        notice_type=notice_type,
    ))


def _gen_id(prefix: str):
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def _platform_order_form_defaults():
    return {
        "platform_name": "校园平台",
        "external_order_no": "",
        "merchant_name": "",
        "receiver_name": "",
        "receiver_phone": "",
        "receiver_address": "",
        "merchant_note": "",
    }


def _pretty_json_text(raw):
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except Exception:
        return raw


def _serialize_event_rows(events):
    return [event.to_dict() for event in events]


def _badge_meta(label: str, badge_class: str = "", hint: str = "", key: str = ""):
    return {
        "key": key or "",
        "label": label or "-",
        "badgeClass": badge_class or "",
        "hint": hint or "",
    }


def _status_meta(value: str, mapping: dict, default_badge_class: str = ""):
    label, badge_class, hint = mapping.get(
        value,
        (value or "-", default_badge_class, ""),
    )
    return _badge_meta(label, badge_class, hint, key=value or "")


def _flag_meta(
    flag: bool,
    true_label: str,
    false_label: str,
    *,
    true_badge_class: str = "b-green",
    false_badge_class: str = "b-gray",
    true_hint: str = "",
    false_hint: str = "",
):
    return _badge_meta(
        true_label if flag else false_label,
        true_badge_class if flag else false_badge_class,
        true_hint if flag else false_hint,
    )


def _format_unix_ts(ts):
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _task_status_meta(status: str):
    mapping = {
        "assigned": ("待执行", "b-yellow", "任务已创建，等待配送员执行"),
        "reassigned": ("待重新投递", "b-yellow", "任务已回退，等待重新投递"),
        "waiting_ai": ("系统确认中", "b-blue", "配送员已入柜，等待系统确认结果"),
        "ai_failed": ("确认未通过", "b-red", "系统暂未确认入柜成功，需要人工处理"),
        "ai_passed": ("已入柜", "b-green", "系统已确认，任务完成入柜"),
        "cancelled": ("已关闭", "b-gray", "任务已取消或已回退关闭"),
    }
    return _status_meta(status, mapping)


def _package_status_meta(status: str):
    mapping = {
        "pending": ("待取件", "b-blue", "包裹已入柜，等待用户取件"),
        "picked": ("已取件", "b-green", "用户已完成取件"),
    }
    return _status_meta(status, mapping)


def _box_status_meta(status: str):
    mapping = {
        "empty": ("空闲", "b-green", "箱体当前空闲，可分配新任务"),
        "reserved": ("预占", "b-yellow", "箱体已分配任务，等待完成入柜"),
        "occupied": ("占用", "b-blue", "箱体已有待取包裹"),
    }
    return _status_meta(status, mapping)


def _locker_command_status_meta(status: str):
    mapping = {
        "accepted": ("已受理", "b-blue", "开门请求已创建并受理"),
        "relay_triggered": ("已触发继电器", "b-blue", "继电器动作已触发"),
        "waiting_door_open": ("等待开门", "b-yellow", "锁反馈已到，等待门磁开门"),
        "door_opened": ("已开门待关门", "b-yellow", "门已打开，等待关门完成闭环"),
        "completed": ("已闭环", "b-green", "柜门链路已完成闭环"),
        "failed": ("执行失败", "b-red", "设备执行失败或离线"),
        "open_timeout": ("开门超时", "b-red", "长时间未收到锁反馈或开门信号"),
        "door_close_timeout": ("关门超时", "b-red", "门已打开，但长时间未关门"),
        "sequence_error": ("顺序异常", "b-red", "门磁回调顺序异常"),
    }
    return _status_meta(status, mapping)


def _locker_business_status_meta(status: str):
    mapping = {
        "pending": ("等待设备反馈", "b-yellow", "开门请求已下发，等待设备继续回传"),
        "waiting_close": ("等待关门", "b-blue", "门已打开，等待关门收口"),
        "completed": ("业务已完成", "b-green", "柜门业务闭环已完成"),
        "exception": ("进入异常", "b-red", "柜门业务进入异常收口"),
    }
    return _status_meta(status, mapping)


def _locker_signal_label(value: str):
    mapping = {
        "open": "已开",
        "closed": "已关",
        "unlocked": "已解锁",
        "locked": "已上锁",
        "ok": "正常",
        "low": "低电平",
        "online": "在线",
        "offline": "离线",
        "failed": "失败",
    }
    return mapping.get((value or "").strip().lower(), value or "-")


def _locker_event_type_label(event_type: str):
    mapping = {
        "command_created": "已创建开门命令",
        "command_ack": "设备已受理命令",
        "lock_opened": "锁反馈已解锁",
        "door_opened": "门磁检测为打开",
        "door_closed": "门磁检测为关闭",
        "command_completed": "开门流程完成",
        "business_completed": "业务闭环完成",
        "command_timeout": "开门超时",
        "door_close_timeout": "关门超时",
        "sequence_error": "回调顺序异常",
        "command_failed": "开门执行失败",
        "task_closed": "任务已关闭",
        "task_reset": "任务已重置",
    }
    return mapping.get((event_type or "").strip(), event_type or "-")


def _locker_source_label(source: str):
    mapping = {
        "backend": "后端",
        "hardware": "硬件",
        "system": "系统",
        "admin": "管理员",
        "courier": "配送员",
        "user": "用户",
    }
    return mapping.get((source or "").strip().lower(), source or "-")


def _locker_trace_status_label_local(kind: str, status: str):
    status_value = (status or "").strip().lower()
    mappings = {
        "recording": {
            "idle": "未开始",
            "recording": "录制中",
            "stopped": "已停止",
        },
        "video": {
            "pending": "待生成",
            "recording": "录制中",
            "ready": "已就绪",
            "missing": "缺失",
            "failed": "失败",
            "not_required": "不需要",
        },
        "snapshot": {
            "pending": "待抓拍",
            "captured": "已抓拍",
            "failed": "失败",
            "not_required": "不需要",
        },
        "result_image": {
            "pending": "待生成",
            "waiting_ai": "等待确认",
            "ready": "已就绪",
            "failed": "失败",
            "not_required": "不需要",
        },
    }
    return mappings.get(kind, {}).get(status_value, status or "-")


def _decorate_locker_command_view(command_view):
    if not command_view:
        return None

    payload = dict(command_view)
    payload["statusMeta"] = _locker_command_status_meta(payload.get("status"))
    payload["businessMeta"] = _locker_business_status_meta(payload.get("businessStatus"))
    payload["doorStateLabel"] = _locker_signal_label(payload.get("doorState"))
    payload["lockFeedbackStateLabel"] = _locker_signal_label(payload.get("lockFeedbackState"))
    payload["controllerPowerStateLabel"] = _locker_signal_label(payload.get("controllerPowerState"))
    payload["lockPowerStateLabel"] = _locker_signal_label(payload.get("lockPowerState"))
    payload["recordingStatusLabelLocal"] = _locker_trace_status_label_local("recording", payload.get("recordingStatus"))
    payload["videoStatusLabelLocal"] = _locker_trace_status_label_local("video", payload.get("videoStatus"))
    payload["snapshotStatusLabelLocal"] = _locker_trace_status_label_local("snapshot", payload.get("snapshotStatus"))
    payload["resultImageStatusLabelLocal"] = _locker_trace_status_label_local("result_image", payload.get("resultImageStatus"))
    return payload


def _decorate_locker_event_view(event_view):
    if not event_view:
        return None

    payload = dict(event_view)
    payload["eventTypeLabel"] = _locker_event_type_label(payload.get("eventType"))
    payload["sourceLabel"] = _locker_source_label(payload.get("source"))
    payload["doorStateLabel"] = _locker_signal_label(payload.get("doorState"))
    payload["lockFeedbackStateLabel"] = _locker_signal_label(payload.get("lockFeedbackState"))
    return payload


def _decorate_hardware_view(hardware_view):
    if not hardware_view:
        return None

    payload = dict(hardware_view)
    payload["latestCommand"] = _decorate_locker_command_view(payload.get("latestCommand"))
    payload["doorStateLabel"] = _locker_signal_label(payload.get("doorState"))
    payload["lockFeedbackStateLabel"] = _locker_signal_label(payload.get("lockFeedbackState"))
    payload["controllerPowerStateLabel"] = _locker_signal_label(payload.get("controllerPowerState"))
    payload["lockPowerStateLabel"] = _locker_signal_label(payload.get("lockPowerState"))
    payload["businessStatusMeta"] = _locker_business_status_meta(payload.get("businessStatus"))
    payload["latestLockEvent"] = _decorate_locker_event_view(payload.get("latestLockEvent"))
    payload["latestDoorEvent"] = _decorate_locker_event_view(payload.get("latestDoorEvent"))
    payload["latestAlertEvent"] = _decorate_locker_event_view(payload.get("latestAlertEvent"))
    return payload



@admin_bp.get("/")
def dashboard():
    user_count = User.query.count()
    courier_count = Courier.query.count()  # 用 Courier 模型统计配送员数量
    box_count = Box.query.count()
    box_empty_count = Box.query.filter_by(status="empty").count()
    box_reserved_count = Box.query.filter_by(status="reserved").count()
    box_occupied_count = Box.query.filter_by(status="occupied").count()
    task_count = Task.query.count()
    task_in_progress_count = (
        Task.query
        .filter(Task.status.in_(list(TASK_ACTIVE_STATUSES)))
        .count()
    )
    pkg_pending = Package.query.filter_by(status="pending").count()
    platform_order_count = PlatformOrder.query.count()
    platform_pending_dispatch_count = PlatformOrder.query.filter_by(status=ORDER_STATUS_PENDING_DISPATCH).count()
    platform_waiting_ai_count = PlatformOrder.query.filter_by(status=ORDER_STATUS_WAITING_AI).count()
    platform_in_progress_count = (
        PlatformOrder.query
        .filter(PlatformOrder.status.in_([ORDER_STATUS_DISPATCHED, ORDER_STATUS_WAITING_AI]))
        .count()
    )
    platform_ready_pickup_count = PlatformOrder.query.filter_by(status=ORDER_STATUS_READY_FOR_PICKUP).count()
    platform_completed_count = PlatformOrder.query.filter_by(status=ORDER_STATUS_COMPLETED).count()
    platform_closed_count = (
        PlatformOrder.query
        .filter(PlatformOrder.status.in_([ORDER_STATUS_CANCELLED, ORDER_STATUS_EXCEPTION]))
        .count()
    )
    integration_cards = get_integration_status_cards()
    recent_orders = [
        _build_platform_order_row(order)
        for order in (
            PlatformOrder.query
            .order_by(PlatformOrder.updated_at.desc(), PlatformOrder.created_at.desc())
            .limit(6)
            .all()
        )
    ]
    recent_alert_events = _serialize_event_rows(
        list_recent_locker_events(limit=6, event_types=LOCKER_ALERT_EVENT_TYPES)
    )
    dashboard_attention_items = [
        {
            "label": "待派单订单",
            "value": platform_pending_dispatch_count,
            "badgeClass": "b-yellow",
            "href": url_for("admin.platform_orders", status="pending_dispatch"),
            "hint": "需要管理员选择配送员并生成任务",
        },
        {
            "label": "待确认 / 人工处理",
            "value": platform_waiting_ai_count,
            "badgeClass": "b-blue" if platform_waiting_ai_count else "b-gray",
            "href": url_for("admin.exceptions"),
            "hint": "配送员已入柜，等待系统确认或人工干预",
        },
        {
            "label": "当前待取包裹",
            "value": pkg_pending,
            "badgeClass": "b-blue" if pkg_pending else "b-gray",
            "href": url_for("admin.packages", status="pending"),
            "hint": "当前待取件的包裹数量",
        },
        {
            "label": "最近柜门告警",
            "value": len(recent_alert_events),
            "badgeClass": "b-red" if recent_alert_events else "b-gray",
            "href": url_for("admin.locker_debug"),
            "hint": "可直接查看柜门超时、离线或顺序异常",
        },
    ]

    return render_template(
        "admin/dashboard.html",
        user_count=user_count,
        courier_count=courier_count,
        box_count=box_count,
        box_empty_count=box_empty_count,
        box_reserved_count=box_reserved_count,
        box_occupied_count=box_occupied_count,
        task_count=task_count,
        task_in_progress_count=task_in_progress_count,
        pkg_pending=pkg_pending,
        platform_order_count=platform_order_count,
        platform_pending_dispatch_count=platform_pending_dispatch_count,
        platform_waiting_ai_count=platform_waiting_ai_count,
        platform_in_progress_count=platform_in_progress_count,
        platform_ready_pickup_count=platform_ready_pickup_count,
        platform_completed_count=platform_completed_count,
        platform_closed_count=platform_closed_count,
        integration_cards=integration_cards,
        dashboard_attention_items=dashboard_attention_items,
        recent_orders=recent_orders,
        recent_alert_events=recent_alert_events,
    )


@admin_bp.get("/users")
def users():
    role = request.args.get("role")

    # ✅你现在 courier 不存在于 User.role 里了，直接跳转到 courier 列表
    if role == "courier":
        return redirect(url_for("admin.couriers_list"))

    q = User.query.filter(User.role == "user")
    if role:
        q = q.filter(User.role == role, User.role == "user")

    users = q.order_by(User.user_id.desc()).all()
    return render_template("admin/users.html", users=users, role=role)

@admin_bp.get("/couriers")
def couriers_list():
    status = (request.args.get("apply_status") or "all").strip()

    q = Courier.query
    if status != "all":
        q = q.filter(Courier.apply_status == status)

    couriers = q.order_by(Courier.created_at.desc()).all()

    return render_template(
        "admin/courier.html",
        couriers=couriers,
        apply_status=status,
    )


@admin_bp.post("/couriers/<int:courier_id>/approve")
def courier_approve(courier_id):
    c = Courier.query.get_or_404(courier_id)
    c.apply_status = "approved"
    c.status = c.status or "available"
    c.reviewed_at = datetime.utcnow()
    c.review_note = None
    db.session.commit()

    back = request.args.get("apply_status") or "all"
    return redirect(url_for("admin.couriers_list", apply_status=back))

@admin_bp.post("/couriers/<int:courier_id>/reject")
def courier_reject(courier_id):
    c = Courier.query.get_or_404(courier_id)
    note = (request.form.get("note") or "").strip()

    c.apply_status = "rejected"
    c.reviewed_at = datetime.utcnow()
    c.review_note = note[:255] if note else "资料不符合要求"

    db.session.commit()

    back = request.args.get("apply_status") or "all"
    return redirect(url_for("admin.couriers_list", apply_status=back))


@admin_bp.get("/boxes")
def boxes():
    boxes = Box.query.order_by(Box.box_no.asc()).all()
    hardware_map = get_box_locker_status_map([box.box_no for box in boxes])
    task_ids = [box.task_id for box in boxes if box.task_id]
    tasks = Task.query.filter(Task.task_id.in_(task_ids)).all() if task_ids else []
    task_map = {task.task_id: task for task in tasks}
    package_ids = [task.package_id for task in tasks if task.package_id]
    packages = Package.query.filter(Package.package_id.in_(package_ids)).all() if package_ids else []
    package_map = {pkg.package_id: pkg for pkg in packages}
    order_ids = [task.platform_order_id for task in tasks if task.platform_order_id]
    orders = PlatformOrder.query.filter(PlatformOrder.platform_order_id.in_(order_ids)).all() if order_ids else []
    order_map = {order.platform_order_id: order for order in orders}
    box_rows = [
        _build_box_row(
            box,
            hardware_map.get(box.box_no),
            task=task_map.get(box.task_id),
            pkg=package_map.get(task_map.get(box.task_id).package_id) if task_map.get(box.task_id) and task_map.get(box.task_id).package_id else None,
            order=order_map.get(task_map.get(box.task_id).platform_order_id) if task_map.get(box.task_id) and task_map.get(box.task_id).platform_order_id else None,
        )
        for box in boxes
    ]
    recent_commands = [
        _decorate_locker_command_view(serialize_locker_command(item))
        for item in list_recent_locker_commands(limit=20)
    ]
    recent_alert_events = _serialize_event_rows(
        list_recent_locker_events(limit=10, event_types=LOCKER_ALERT_EVENT_TYPES)
    )
    return render_template(
        "admin/boxes.html",
        box_rows=box_rows,
        recent_commands=recent_commands,
        recent_alert_events=recent_alert_events,
        box_alert_count=sum(1 for row in box_rows if row["hardware"] and row["hardware"].get("hasAlert")),
        box_empty_count=sum(1 for row in box_rows if row["box"].status == "empty"),
        box_reserved_count=sum(1 for row in box_rows if row["box"].status == "reserved"),
        box_occupied_count=sum(1 for row in box_rows if row["box"].status == "occupied"),
    )


@admin_bp.get("/locker-debug")
def locker_debug():
    box_no = (request.args.get("box_no") or "").strip()
    command_id = (request.args.get("command_id") or "").strip()
    limit_raw = (request.args.get("limit") or "20").strip()
    try:
        limit = max(5, min(int(limit_raw), 100))
    except Exception:
        limit = 20

    boxes = Box.query.order_by(Box.box_no.asc()).all()
    hardware_map = get_box_locker_status_map([box.box_no for box in boxes])
    recent_commands = [
        _decorate_locker_command_view(serialize_locker_command(item))
        for item in list_recent_locker_commands(limit=limit, box_no=box_no or None)
    ]
    recent_lock_events = _serialize_event_rows(
        list_recent_locker_events(limit=limit, box_no=box_no or None, event_types=LOCKER_LOCK_EVENT_TYPES)
    )
    recent_lock_events = [_decorate_locker_event_view(item) for item in recent_lock_events]
    recent_door_events = _serialize_event_rows(
        list_recent_locker_events(limit=limit, box_no=box_no or None, event_types=LOCKER_DOOR_EVENT_TYPES)
    )
    recent_door_events = [_decorate_locker_event_view(item) for item in recent_door_events]
    recent_alert_events = _serialize_event_rows(
        list_recent_locker_events(limit=limit, box_no=box_no or None, event_types=LOCKER_ALERT_EVENT_TYPES)
    )
    recent_alert_events = [_decorate_locker_event_view(item) for item in recent_alert_events]

    selected_command = get_locker_command(command_id) if command_id else None
    selected_command_view = (
        _decorate_locker_command_view(serialize_locker_command(selected_command, include_events=True, event_limit=30))
        if selected_command else None
    )
    if selected_command_view and selected_command_view.get("events"):
        selected_command_view["events"] = [
            _decorate_locker_event_view(item)
            for item in selected_command_view["events"]
        ]

    selected_box_no = box_no or (selected_command.box_no if selected_command else "")
    selected_box = Box.query.get(selected_box_no) if selected_box_no else None
    selected_hardware = (
        _decorate_hardware_view(get_box_locker_status(selected_box_no, event_limit=15))
        if selected_box_no else None
    )

    return render_template(
        "admin/locker_debug.html",
        box_no=box_no,
        command_id=command_id,
        limit=limit,
        box_rows=[{"box": box, "hardware": _decorate_hardware_view(hardware_map.get(box.box_no))} for box in boxes],
        recent_commands=recent_commands,
        recent_lock_events=recent_lock_events,
        recent_door_events=recent_door_events,
        recent_alert_events=recent_alert_events,
        selected_command=selected_command_view,
        selected_command_request_pretty=_pretty_json_text(getattr(selected_command, "request_payload", "")),
        selected_command_callback_pretty=_pretty_json_text(getattr(selected_command, "last_callback_payload", "")),
        selected_box=selected_box,
        selected_hardware=selected_hardware,
    )


@admin_bp.get("/tasks")
def tasks():
    platform_order_id = (request.args.get("platform_order_id") or "").strip()

    q = Task.query
    selected_order = None
    if platform_order_id:
        q = q.filter_by(platform_order_id=platform_order_id)
        selected_order = PlatformOrder.query.get(platform_order_id)

    tasks = q.order_by(Task.task_id.desc()).all()
    order_ids = [t.platform_order_id for t in tasks if t.platform_order_id]
    orders = PlatformOrder.query.filter(PlatformOrder.platform_order_id.in_(order_ids)).all() if order_ids else []
    order_map = {order.platform_order_id: order for order in orders}

    task_rows = [_build_task_row(task, order_map) for task in tasks]
    task_stats = {
        "total": len(task_rows),
        "pending": sum(1 for row in task_rows if ((row.get("task_stage") or {}).get("key") or "") == "pending"),
        "in_progress": sum(1 for row in task_rows if ((row.get("task_stage") or {}).get("key") or "") == "in_progress"),
        "completed": sum(1 for row in task_rows if ((row.get("task_stage") or {}).get("key") or "") == "completed"),
        "closed": sum(1 for row in task_rows if ((row.get("task_stage") or {}).get("key") or "") == "closed"),
    }

    return render_template(
        "admin/tasks.html",
        task_rows=task_rows,
        task_stats=task_stats,
        platform_order_id=platform_order_id,
        selected_order=selected_order,
    )


@admin_bp.get("/exceptions")
def exceptions():
    notice = (request.args.get("notice") or "").strip()
    notice_type = (request.args.get("notice_type") or "success").strip()
    approved_couriers, courier_user_map = _approved_courier_context()

    submitted_task_ids = [
        row[0]
        for row in (
            db.session.query(AdminLog.task_id)
            .filter(
                AdminLog.action == "task.submitted_to_admin",
                AdminLog.task_id.isnot(None),
            )
            .distinct()
            .all()
        )
        if row[0]
    ]
    submitted_admin_tasks = (
        Task.query
        .filter(
            Task.task_id.in_(submitted_task_ids or ["__none__"]),
            Task.exception_status == "pending",
        )
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .all()
    )
    submitted_order_ids = [task.platform_order_id for task in submitted_admin_tasks if task.platform_order_id]
    submitted_orders = (
        PlatformOrder.query
        .filter(PlatformOrder.platform_order_id.in_(submitted_order_ids))
        .all()
        if submitted_order_ids else []
    )
    submitted_order_map = {order.platform_order_id: order for order in submitted_orders}
    submitted_user_map = _load_user_map(
        [task.courier_openid for task in submitted_admin_tasks] +
        [task.receiver_openid for task in submitted_admin_tasks] +
        [order.receiver_openid for order in submitted_orders]
    )
    submitted_courier_map = _load_courier_map([task.courier_openid for task in submitted_admin_tasks])
    admin_exception_rows = [
        _build_admin_exception_row(task, submitted_order_map, submitted_user_map, submitted_courier_map)
        for task in submitted_admin_tasks
    ]

    active_orders = (
        PlatformOrder.query
        .filter(PlatformOrder.status.in_([ORDER_STATUS_WAITING_AI, ORDER_STATUS_PENDING_DISPATCH]))
        .order_by(PlatformOrder.updated_at.desc(), PlatformOrder.created_at.desc())
        .all()
    )
    closed_orders = (
        PlatformOrder.query
        .filter(PlatformOrder.status.in_([ORDER_STATUS_EXCEPTION, ORDER_STATUS_CANCELLED]))
        .order_by(PlatformOrder.closed_at.desc(), PlatformOrder.updated_at.desc())
        .limit(30)
        .all()
    )

    waiting_rows = []
    rollback_rows = []
    for order in active_orders:
        row = _build_platform_order_row(order)
        task = row["task"]
        if order.status == ORDER_STATUS_WAITING_AI and task and task.status in ("waiting_ai", "ai_failed"):
            waiting_rows.append(row)
        elif order.status == ORDER_STATUS_PENDING_DISPATCH and task and task.status == "cancelled":
            rollback_rows.append(row)

    boxes = Box.query.order_by(Box.box_no.asc()).all()
    hardware_map = get_box_locker_status_map([box.box_no for box in boxes]) if boxes else {}
    task_ids = [box.task_id for box in boxes if box.task_id]
    tasks = Task.query.filter(Task.task_id.in_(task_ids)).all() if task_ids else []
    task_map = {task.task_id: task for task in tasks}
    order_ids = [task.platform_order_id for task in tasks if task.platform_order_id]
    orders = PlatformOrder.query.filter(PlatformOrder.platform_order_id.in_(order_ids)).all() if order_ids else []
    order_map = {order.platform_order_id: order for order in orders}
    locker_alert_boxes = [
        {
            "box": box,
            "hardware": _decorate_hardware_view(hardware_map.get(box.box_no)),
            "task": task_map.get(box.task_id),
            "order": order_map.get(task_map.get(box.task_id).platform_order_id) if task_map.get(box.task_id) and task_map.get(box.task_id).platform_order_id else None,
        }
        for box in boxes
        if hardware_map.get(box.box_no) and hardware_map.get(box.box_no).get("hasAlert")
    ]
    recent_hardware_alerts = _serialize_event_rows(
        list_recent_locker_events(limit=12, event_types=LOCKER_ALERT_EVENT_TYPES)
    )
    recent_hardware_alerts = [_decorate_locker_event_view(item) for item in recent_hardware_alerts]

    return render_template(
        "admin/exceptions.html",
        admin_exception_rows=admin_exception_rows,
        waiting_rows=waiting_rows,
        rollback_rows=rollback_rows,
        closed_rows=[_build_platform_order_row(order) for order in closed_orders],
        locker_alert_boxes=locker_alert_boxes,
        recent_hardware_alerts=recent_hardware_alerts,
        approved_couriers=approved_couriers,
        courier_user_map=courier_user_map,
        notice=notice,
        notice_type=notice_type,
    )


@admin_bp.get("/platform/orders")
def platform_orders():
    status = (request.args.get("status") or "all").strip()
    notice = (request.args.get("notice") or "").strip()
    notice_type = (request.args.get("notice_type") or "success").strip()

    q = PlatformOrder.query
    if status != "all":
        q = q.filter_by(status=status)

    orders = q.order_by(PlatformOrder.created_at.desc()).all()
    approved_couriers, courier_user_map = _approved_courier_context()
    order_rows = [_build_platform_order_row(order) for order in orders]
    order_stats = {
        "filtered": len(order_rows),
        "all": PlatformOrder.query.count(),
        "pending_dispatch": PlatformOrder.query.filter_by(status=ORDER_STATUS_PENDING_DISPATCH).count(),
        "dispatched": PlatformOrder.query.filter_by(status=ORDER_STATUS_DISPATCHED).count(),
        "waiting_ai": PlatformOrder.query.filter_by(status=ORDER_STATUS_WAITING_AI).count(),
        "ready_for_pickup": PlatformOrder.query.filter_by(status=ORDER_STATUS_READY_FOR_PICKUP).count(),
        "completed": PlatformOrder.query.filter_by(status=ORDER_STATUS_COMPLETED).count(),
        "closed": (
            PlatformOrder.query
            .filter(PlatformOrder.status.in_([ORDER_STATUS_CANCELLED, ORDER_STATUS_EXCEPTION]))
            .count()
        ),
    }

    return render_template(
        "admin/platform_orders.html",
        order_rows=order_rows,
        order_stats=order_stats,
        approved_couriers=approved_couriers,
        courier_user_map=courier_user_map,
        current_status=status,
        notice=notice,
        notice_type=notice_type,
    )


@admin_bp.get("/platform/orders/new")
def new_platform_order():
    return render_template(
        "admin/platform_order_new.html",
        form_data=_platform_order_form_defaults(),
        notice=(request.args.get("notice") or "").strip(),
        notice_type=(request.args.get("notice_type") or "success").strip(),
    )


@admin_bp.post("/platform/orders/create")
def create_platform_order():
    form_data = {
        "platform_name": (request.form.get("platform_name") or "").strip(),
        "external_order_no": (request.form.get("external_order_no") or "").strip(),
        "merchant_name": (request.form.get("merchant_name") or "").strip(),
        "receiver_name": (request.form.get("receiver_name") or "").strip(),
        "receiver_phone": (request.form.get("receiver_phone") or "").strip(),
        "receiver_address": (request.form.get("receiver_address") or "").strip(),
        "merchant_note": (request.form.get("merchant_note") or "").strip(),
    }

    if not form_data["platform_name"]:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="平台名称不能为空",
            notice_type="error",
        )

    if not form_data["merchant_name"]:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="商家名称不能为空",
            notice_type="error",
        )

    if not form_data["receiver_name"]:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="收件人姓名不能为空",
            notice_type="error",
        )

    phone_match = match_user_by_phone(form_data["receiver_phone"])
    if phone_match["status"] in {PHONE_MISSING, PHONE_INVALID}:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="收件人手机号格式不正确",
            notice_type="error",
        )

    if not form_data["receiver_address"]:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="配送地址不能为空",
            notice_type="error",
        )

    if not form_data["external_order_no"]:
        form_data["external_order_no"] = _gen_id("EXT")

    exists = PlatformOrder.query.filter_by(
        platform_name=form_data["platform_name"],
        external_order_no=form_data["external_order_no"],
    ).first()
    if exists:
        return render_template(
            "admin/platform_order_new.html",
            form_data=form_data,
            notice="该平台名称下的订单号已存在，请更换后再试",
            notice_type="error",
        )

    phone_match = match_user_by_phone(form_data["receiver_phone"])
    receiver_openid = phone_match["openid"] if phone_match["status"] == PHONE_MATCHED else ""
    receiver_match_note = (phone_match["message"] or "").strip() if phone_match["status"] != PHONE_MATCHED else ""

    payload = {
        "platformName": form_data["platform_name"],
        "externalOrderNo": form_data["external_order_no"],
        "merchantName": form_data["merchant_name"],
        "receiverName": form_data["receiver_name"],
        "receiverPhone": form_data["receiver_phone"],
        "receiverOpenid": receiver_openid or "",
        "receiverAddress": form_data["receiver_address"],
        "merchantNote": form_data["merchant_note"],
        "source": "admin_mock",
    }

    order = PlatformOrder(
        platform_order_id=_gen_id("PO"),
        platform_name=form_data["platform_name"],
        external_order_no=form_data["external_order_no"],
        source="admin_mock",
        status=ORDER_STATUS_PENDING_DISPATCH,
        receiver_name=form_data["receiver_name"],
        receiver_phone=form_data["receiver_phone"],
        receiver_address=form_data["receiver_address"],
        receiver_openid=receiver_openid or None,
        merchant_name=form_data["merchant_name"],
        merchant_note=form_data["merchant_note"],
        status_note=receiver_match_note[:255] if receiver_match_note else None,
        raw_payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    db.session.add(order)
    ensure_order_qr(order)
    log_action(
        "platform_order.created",
        actor_type="admin",
        actor_openid=_current_admin_openid(),
        platform_order_id=order.platform_order_id,
        detail=f"后台创建平台订单：{order.platform_name} / {order.external_order_no}",
    )
    db.session.commit()
    emit_platform_event("order_received", order=order)

    return _platform_orders_redirect(
        "pending_dispatch",
        "平台订单创建成功，已进入待派单列表" if receiver_openid else f"订单已创建，但当前手机号未匹配到用户：{receiver_match_note}",
        "success" if receiver_openid else "info",
    )


@admin_bp.get("/platform/orders/<string:platform_order_id>")
def platform_order_detail(platform_order_id):
    order = PlatformOrder.query.get_or_404(platform_order_id)
    if ensure_order_qr(order):
        db.session.commit()
    approved_couriers, courier_user_map = _approved_courier_context()
    row = _build_platform_order_row(order)
    task = row["task"]
    pkg = row["package"]
    logs = (
        AdminLog.query
        .filter_by(platform_order_id=platform_order_id)
        .order_by(AdminLog.created_at.desc(), AdminLog.log_id.desc())
        .limit(20)
        .all()
    )
    current_box_no = row.get("current_box_no")
    current_box = Box.query.get(current_box_no) if current_box_no else None
    current_box_hardware = get_box_locker_status(current_box_no, event_limit=10) if current_box_no else None
    current_box_command = _decorate_locker_command_view(((current_box_hardware or {}).get("latestCommand") or None))
    package_deposit_command = _decorate_locker_command_view(serialize_locker_command(_latest_package_command(pkg.package_id, scene="deposit"))) if pkg else None
    package_pickup_command = _decorate_locker_command_view(serialize_locker_command(_latest_package_command(pkg.package_id, scene="pickup"))) if pkg else None
    remind_summary = None
    if pkg:
        remind_total = RemindEvent.query.filter_by(package_id=pkg.package_id).count()
        remind_sent = (
            RemindEvent.query
            .filter_by(package_id=pkg.package_id)
            .filter(RemindEvent.sent_at.isnot(None))
            .count()
        )
        remind_read = (
            RemindEvent.query
            .filter_by(package_id=pkg.package_id)
            .filter(RemindEvent.read_at.isnot(None))
            .count()
        )
        latest_remind = (
            RemindEvent.query
            .filter_by(package_id=pkg.package_id)
            .order_by(RemindEvent.created_at.desc(), RemindEvent.id.desc())
            .first()
        )
        remind_summary = {
            "total": remind_total,
            "sent": remind_sent,
            "read": remind_read,
            "latest": latest_remind,
        }

    raw_payload_pretty = order.raw_payload or ""
    if raw_payload_pretty:
        try:
            raw_payload_pretty = json.dumps(json.loads(raw_payload_pretty), ensure_ascii=False, indent=2)
        except Exception:
            pass

    return render_template(
        "admin/platform_order_detail.html",
        row=row,
        approved_couriers=approved_couriers,
        courier_user_map=courier_user_map,
        current_box=current_box,
        current_box_status_meta=_box_status_meta(current_box.status) if current_box else None,
        current_box_hardware=current_box_hardware,
        current_box_command=current_box_command,
        package_deposit_command=package_deposit_command,
        package_pickup_command=package_pickup_command,
        remind_summary=remind_summary,
        package_arrived_at_text=_format_unix_ts(pkg.arrived_at) if pkg else "-",
        package_picked_at_text=_format_unix_ts(pkg.picked_at) if pkg else "-",
        raw_payload_pretty=raw_payload_pretty,
        logs=[serialize_log(log) for log in logs],
        qr_image_url=url_for("artifact_file", artifact_path=order.qr_image_path) if order.qr_image_path else "",
        notice=(request.args.get("notice") or "").strip(),
        notice_type=(request.args.get("notice_type") or "success").strip(),
    )


@admin_bp.post("/platform/orders/<string:platform_order_id>/dispatch")
def admin_dispatch_platform_order(platform_order_id):
    order = PlatformOrder.query.get_or_404(platform_order_id)
    courier_openid = (request.form.get("courier_openid") or "").strip()
    back_to = (request.form.get("back_to") or "list").strip()
    back_status = (request.form.get("back_status") or "all").strip()

    courier = Courier.query.filter_by(openid=courier_openid, apply_status="approved").first()
    if not courier:
        return _dispatch_redirect(
            platform_order_id,
            back_to,
            back_status,
            "请选择已审核通过的配送员",
            "error",
        )

    try:
        task, created = dispatch_order_to_task(order, courier_openid)
    except ValueError as exc:
        return _dispatch_redirect(
            platform_order_id,
            back_to,
            back_status,
            str(exc),
            "error",
        )
    except RuntimeError as exc:
        return _dispatch_redirect(
            platform_order_id,
            back_to,
            back_status,
            str(exc),
            "error",
        )

    db.session.commit()
    if created:
        log_action(
            "platform_order.dispatched",
            actor_type="admin",
            actor_openid=_current_admin_openid(),
            platform_order_id=order.platform_order_id,
            task_id=task.task_id,
            detail=f"管理员派单给配送员：{courier_openid}",
        )
        db.session.commit()
        emit_platform_event("order_dispatched", order=order, task=task)
        emit_hardware_event("task_created", order=order, task=task)
        notice = "派单成功，已生成投递任务"
    else:
        notice = "该平台订单已存在有效任务，未重复生成"

    return _dispatch_redirect(
        platform_order_id,
        back_to,
        back_status,
        notice,
        "success",
    )


@admin_bp.post("/platform/orders/<string:platform_order_id>/close")
def admin_close_platform_order(platform_order_id):
    order = PlatformOrder.query.get_or_404(platform_order_id)
    target_status = (request.form.get("target_status") or "").strip()
    note = (request.form.get("note") or "").strip()
    back_to = (request.form.get("back_to") or "detail").strip()
    back_status = (request.form.get("back_status") or "all").strip()

    try:
        closed_order, task, pkg = close_order(order, target_status, note)
    except ValueError as exc:
        return _dispatch_redirect(
            platform_order_id,
            back_to,
            back_status,
            str(exc),
            "error",
        )

    db.session.commit()
    log_action(
        "platform_order.cancelled" if target_status == ORDER_STATUS_CANCELLED else "platform_order.exception_closed",
        actor_type="admin",
        actor_openid=_current_admin_openid(),
        platform_order_id=closed_order.platform_order_id,
        task_id=getattr(task, "task_id", None),
        package_id=getattr(pkg, "package_id", None),
        detail=closed_order.status_note,
    )
    db.session.commit()
    event_name = "order_cancelled" if target_status == ORDER_STATUS_CANCELLED else "order_exception_closed"
    emit_platform_event(event_name, order=closed_order, task=task, pkg=pkg)
    emit_hardware_event("task_closed", order=closed_order, task=task, pkg=pkg)

    notice = "订单已取消" if target_status == ORDER_STATUS_CANCELLED else "订单已按异常流程收口"
    return _dispatch_redirect(
        platform_order_id,
        back_to,
        back_status,
        notice,
        "success",
    )


@admin_bp.post("/tasks/<string:task_id>/retry")
def admin_retry_task(task_id):
    task = Task.query.get_or_404(task_id)
    back_to = (request.form.get("back_to") or "exceptions").strip()
    note = (request.form.get("note") or "").strip()

    try:
        order, _ = reset_task_for_retry(task, note)
    except ValueError as exc:
        return _task_action_redirect(task, back_to, str(exc), "error")

    log_action(
        "task.reset_by_admin",
        actor_type="admin",
        actor_openid=_current_admin_openid(),
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        detail=note or "管理员将任务重置为待重新投递",
    )
    db.session.commit()
    emit_platform_event("order_redispatched", order=order, task=task)

    return _task_action_redirect(task, back_to, "已回退到待重新投递，可继续由配送员重试", "success")


@admin_bp.post("/tasks/<string:task_id>/exception-note")
def admin_update_exception_note(task_id):
    task = Task.query.get_or_404(task_id)
    back_to = (request.form.get("back_to") or "exceptions").strip()
    action_kind = (request.form.get("action_kind") or "").strip()
    note = (request.form.get("note") or "").strip()

    action_defaults = {
        "repair": ("管理员维修中", "task.admin_repair_started", "已记录为管理员维修中"),
        "contact_buyer": ("待联系买家", "task.admin_contact_buyer", "已记录为待联系买家"),
        "resolved": ("已处理完成", "task.admin_exception_resolved", "已标记为处理完成"),
    }
    default_note, action_name, success_notice = action_defaults.get(
        action_kind,
        ("管理员已更新异常说明", "task.admin_exception_note_updated", "异常说明已更新"),
    )
    final_note = note or default_note

    if action_kind == "resolved":
        mark_task_exception_resolved(task, final_note, force=True)
    else:
        mark_task_exception_pending(task, final_note)

    log_action(
        action_name,
        actor_type="admin",
        actor_openid=_current_admin_openid(),
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        package_id=task.package_id,
        detail=final_note,
    )
    db.session.commit()
    return _task_action_redirect(task, back_to, success_notice, "success")


@admin_bp.post("/tasks/<string:task_id>/rollback")
def admin_rollback_task(task_id):
    task = Task.query.get_or_404(task_id)
    back_to = (request.form.get("back_to") or "exceptions").strip()
    note = (request.form.get("note") or "").strip()

    try:
        order, _ = rollback_task_to_pending_dispatch(task, note or "管理员回退任务，等待重新派单")
    except ValueError as exc:
        return _task_action_redirect(task, back_to, str(exc), "error")

    log_action(
        "task.rolled_back_by_admin",
        actor_type="admin",
        actor_openid=_current_admin_openid(),
        platform_order_id=task.platform_order_id,
        task_id=task.task_id,
        detail=note or "管理员将任务回退到待派单",
    )
    db.session.commit()
    emit_platform_event("order_back_to_pending_dispatch", order=order, task=task)

    return _task_action_redirect(task, back_to, "任务已回退到待派单，可重新分配配送员", "success")


@admin_bp.get("/packages")
def packages():
    platform_order_id = (request.args.get("platform_order_id") or "").strip()
    task_id = (request.args.get("task_id") or "").strip()
    status = (request.args.get("status") or "all").strip()

    q = Package.query
    selected_task = Task.query.get(task_id) if task_id else None
    selected_order = PlatformOrder.query.get(platform_order_id) if platform_order_id else None

    if platform_order_id:
        order_task_ids = [
            task.task_id
            for task in Task.query.filter_by(platform_order_id=platform_order_id).all()
        ]
        q = q.filter(Package.task_id.in_(order_task_ids or ["__none__"]))

    if task_id:
        q = q.filter_by(task_id=task_id)
        if selected_task and selected_task.platform_order_id and not selected_order:
            selected_order = PlatformOrder.query.get(selected_task.platform_order_id)

    if status != "all":
        q = q.filter_by(status=status)

    package_items = q.order_by(Package.created_at.desc()).all()
    task_ids = [pkg.task_id for pkg in package_items if pkg.task_id]
    tasks = Task.query.filter(Task.task_id.in_(task_ids)).all() if task_ids else []
    task_map = {task.task_id: task for task in tasks}
    order_ids = [task.platform_order_id for task in tasks if task.platform_order_id]
    orders = PlatformOrder.query.filter(PlatformOrder.platform_order_id.in_(order_ids)).all() if order_ids else []
    order_map = {order.platform_order_id: order for order in orders}
    package_rows = [_build_package_row(pkg, task_map, order_map) for pkg in package_items]
    package_stats = {
        "total": len(package_rows),
        "pending": sum(1 for row in package_rows if row["package"].status == "pending"),
        "picked": sum(1 for row in package_rows if row["package"].status == "picked"),
    }

    return render_template(
        "admin/packages.html",
        package_rows=package_rows,
        package_stats=package_stats,
        selected_order=selected_order,
        selected_task=selected_task,
        current_status=status,
        platform_order_id=platform_order_id,
        task_id=task_id,
    )

@admin_bp.post("/packages/<string:package_id>/remind")
def admin_remind_package(package_id):
    pkg = Package.query.get_or_404(package_id)

    # 只有待取才提醒（你可按需删掉这个限制）
    # if pkg.status != "pending":
    #     return redirect(url_for("admin.packages"))

    note = (request.form.get("note") or "").strip()
    msg = note if note else "管理员提醒：请及时取餐"

    if not pkg.receiver_openid:
        return redirect(url_for("admin.packages"))

    ev = RemindEvent(
        openid=pkg.receiver_openid,
        package_id=pkg.package_id,
        due_at=datetime.utcnow(),
        message=msg[:255],
        source="admin",
        sent_at=None,
        read_at=None
    )
    db.session.add(ev)
    db.session.commit()

    return redirect(url_for(
        "admin.packages",
        platform_order_id=(request.args.get("platform_order_id") or "").strip(),
        task_id=(request.args.get("task_id") or "").strip(),
        status=(request.args.get("status") or "all").strip(),
    ))

@admin_bp.get("/users/<string:openid>/pickup_profile")
def user_pickup_profile(openid):
    # 1) 用户信息
    u = User.query.filter_by(openid=openid).first()

    # 2) 最近5次取件历史
    hist = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .limit(5).all())

    # 3) 当前策略（没有就默认 auto）
    policy = RemindPolicy.query.filter_by(openid=openid).first()
    if not policy:
        policy = RemindPolicy(openid=openid, mode="auto", enabled=True)
        db.session.add(policy)
        db.session.commit()

    # 4) 最近10条提醒（看管理员有没有发成功）
    evs = (RemindEvent.query
           .filter_by(openid=openid)
           .order_by(RemindEvent.created_at.desc())
           .limit(10).all())

    # 5) 当前待取数量（加分展示）
    pending_cnt = Package.query.filter_by(receiver_openid=openid, status="pending").count()

    return render_template(
        "admin/user_pickup_profile.html",
        u=u,
        openid=openid,
        pending_cnt=pending_cnt,
        hist=hist,
        policy=policy,
        evs=evs,
    )

@admin_bp.post("/users/<string:openid>/pickup_profile/save_policy")
def save_policy(openid):
    policy = RemindPolicy.query.filter_by(openid=openid).first()
    if not policy:
        policy = RemindPolicy(openid=openid, mode="auto", enabled=True)
        db.session.add(policy)

    mode = (request.form.get("mode") or "auto").strip()
    fixed_count_raw = (request.form.get("fixed_count") or "").strip()
    offsets_json = (request.form.get("offsets_json") or "").strip()

    policy.mode = mode if mode in ("auto", "fixed", "custom") else "auto"

    # fixed_count 校验
    if fixed_count_raw.isdigit():
        fc = int(fixed_count_raw)
        if 1 <= fc <= 5:
            policy.fixed_count = fc
        else:
            policy.fixed_count = None
    else:
        policy.fixed_count = None

    # offsets_json 简单校验：必须是 JSON array
    if offsets_json:
        try:
            arr = json.loads(offsets_json)
            if isinstance(arr, list) and all(isinstance(x, int) for x in arr):
                policy.offsets_json = json.dumps(arr[:10])  # 防止太长
            else:
                policy.offsets_json = None
        except Exception:
            policy.offsets_json = None
    else:
        policy.offsets_json = None

    db.session.commit()
    return redirect(url_for("admin.user_pickup_profile", openid=openid))


@admin_bp.post("/users/<string:openid>/pickup_profile/manual_remind")
def manual_remind_from_profile(openid):
    msg = (request.form.get("message") or "").strip() or "管理员提醒：请及时取餐"

    ev = RemindEvent(
        openid=openid,
        package_id=None,
        due_at=datetime.utcnow(),
        message=msg[:255],
        source="admin",
        sent_at=None,
        read_at=None
    )
    db.session.add(ev)
    db.session.commit()

    return redirect(url_for("admin.user_pickup_profile", openid=openid))
