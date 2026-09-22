import re

from flask import current_app

from models import Package, PlatformOrder, Task, User


PHONE_MATCHED = "matched"
PHONE_UNMATCHED = "unmatched"
PHONE_AMBIGUOUS = "ambiguous"
PHONE_MISSING = "missing"
PHONE_INVALID = "invalid"

REASON_MISSING_PHONE = "missing_phone"
REASON_INVALID_PHONE_FORMAT = "invalid_phone_format"
REASON_USER_NOT_FOUND = "user_not_found"
REASON_DUPLICATE_PHONE_MATCH = "duplicate_phone_match"


def match_user_by_phone(phone: str):
    value = (phone or "").strip()
    if not value:
        return {
            "status": PHONE_MISSING,
            "reasonCode": REASON_MISSING_PHONE,
            "openid": "",
            "user": None,
            "users": [],
            "message": "缺少收件人手机号",
        }

    if not re.fullmatch(r"\d{11}", value):
        return {
            "status": PHONE_INVALID,
            "reasonCode": REASON_INVALID_PHONE_FORMAT,
            "openid": "",
            "user": None,
            "users": [],
            "message": "手机号格式不正确",
        }

    users = (
        User.query
        .filter(User.phone_number == value)
        .order_by(User.created_at.asc(), User.user_id.asc())
        .all()
    )

    unique_users = []
    seen_openids = set()
    for user in users:
        openid = (user.openid or "").strip()
        if not openid or openid in seen_openids:
            continue
        seen_openids.add(openid)
        unique_users.append(user)

    if len(unique_users) == 1:
        return {
            "status": PHONE_MATCHED,
            "reasonCode": "",
            "openid": unique_users[0].openid,
            "user": unique_users[0],
            "users": unique_users,
            "message": "",
        }

    if not unique_users:
        return {
            "status": PHONE_UNMATCHED,
            "reasonCode": REASON_USER_NOT_FOUND,
            "openid": "",
            "user": None,
            "users": [],
            "message": "该手机号未绑定用户，无法继续创建任务",
        }

    return {
        "status": PHONE_AMBIGUOUS,
        "reasonCode": REASON_DUPLICATE_PHONE_MATCH,
        "openid": "",
        "user": None,
        "users": unique_users,
        "message": "该手机号匹配到多个用户，请先清理数据",
    }


def phone_match_http_code(match_result) -> int:
    status = (match_result or {}).get("status")
    if status in {PHONE_MISSING, PHONE_INVALID}:
        return 400
    return 409


def resolve_user_openid_by_phone(phone: str) -> str:
    match = match_user_by_phone(phone)
    return (match.get("openid") or "").strip()


def get_explicit_demo_receiver_openid() -> str:
    if not current_app.config.get("USE_MOCK", True):
        return ""
    return (current_app.config.get("DEMO_DEFAULT_RECEIVER_OPENID") or "").strip()


def resolve_order_receiver_openid(
    order: PlatformOrder,
    *,
    allow_phone_lookup: bool = True,
    allow_demo_fallback: bool = False,
) -> str:
    if not order:
        return ""

    openid = (order.receiver_openid or "").strip()
    if openid:
        return openid

    if allow_phone_lookup:
        openid = resolve_user_openid_by_phone(order.receiver_phone)
        if openid:
            return openid

    if allow_demo_fallback:
        return get_explicit_demo_receiver_openid()
    return ""


def resolve_task_receiver_openid(
    task: Task,
    *,
    allow_phone_lookup: bool = False,
    allow_demo_fallback: bool = False,
) -> str:
    if not task:
        return ""

    openid = (getattr(task, "receiver_openid", "") or "").strip()
    if openid:
        return openid

    order = PlatformOrder.query.get(task.platform_order_id) if getattr(task, "platform_order_id", None) else None
    openid = resolve_order_receiver_openid(
        order,
        allow_phone_lookup=False,
        allow_demo_fallback=allow_demo_fallback,
    )
    if openid:
        return openid

    if allow_demo_fallback:
        return get_explicit_demo_receiver_openid()
    return ""


def resolve_package_receiver_openid(
    pkg: Package,
    *,
    allow_phone_lookup: bool = False,
    allow_demo_fallback: bool = False,
) -> str:
    if not pkg:
        return ""

    openid = (pkg.receiver_openid or "").strip()
    if openid:
        return openid

    task = Task.query.get(pkg.task_id) if getattr(pkg, "task_id", None) else None
    openid = resolve_task_receiver_openid(
        task,
        allow_phone_lookup=False,
        allow_demo_fallback=allow_demo_fallback,
    )
    if openid:
        return openid

    if allow_demo_fallback:
        return get_explicit_demo_receiver_openid()
    return ""


def backfill_order_receiver_openid(order: PlatformOrder, openid: str) -> bool:
    if not order:
        return False

    value = (openid or "").strip()
    if not value or (order.receiver_openid or "").strip():
        return False

    order.receiver_openid = value
    return True


def backfill_task_receiver_openid(task: Task, openid: str) -> bool:
    if not task:
        return False

    value = (openid or "").strip()
    if not value or (getattr(task, "receiver_openid", "") or "").strip():
        return False

    task.receiver_openid = value
    return True


def backfill_package_receiver_openid(pkg: Package, openid: str) -> bool:
    if not pkg:
        return False

    value = (openid or "").strip()
    if not value or (pkg.receiver_openid or "").strip():
        return False

    pkg.receiver_openid = value
    return True


def backfill_package_receiver_chain(pkg: Package, openid: str) -> bool:
    changed = backfill_package_receiver_openid(pkg, openid)
    task = Task.query.get(pkg.task_id) if pkg and getattr(pkg, "task_id", None) else None
    if backfill_task_receiver_openid(task, openid):
        changed = True

    order = PlatformOrder.query.get(task.platform_order_id) if task and getattr(task, "platform_order_id", None) else None
    if backfill_order_receiver_openid(order, openid):
        changed = True
    return changed
