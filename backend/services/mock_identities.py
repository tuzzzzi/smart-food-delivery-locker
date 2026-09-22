from datetime import datetime

from extensions import db
from models import Admin, Courier, User


DEFAULT_MOCK_IDENTITY_KEY = "mock_wechat_user"

MOCK_IDENTITIES = [
    {
        "key": "mock_wechat_user",
        "label": "MockWechatUser",
        "openid": "mock_wechat_user",
        "username": "",
        "user_role": "user",
        "phone_number": "",
        "email": "",
        "audiences": [],
        "description": "Mock user for WeChat one-tap login",
        "badge_text": "Wechat user",
        "clear_courier": True,
        "reset_profile": True,
    },
    {
        "key": "user_a",
        "label": "用户A",
        "openid": "mock_user_a",
        "username": "用户A",
        "user_role": "user",
        "phone_number": "13800000002",
        "email": "usera@example.test",
        "receiver_name": "用户A",
        "audiences": ["miniapp"],
        "description": "取件用户测试账号 A",
        "badge_text": "取件用户",
        "demo_group": "pickup_user",
        "clear_courier": True,
    },
    {
        "key": "user_b",
        "label": "用户B",
        "openid": "mock_user_b",
        "username": "用户B",
        "user_role": "user",
        "phone_number": "13800000003",
        "email": "userb@example.test",
        "receiver_name": "用户B",
        "audiences": ["miniapp"],
        "description": "取件用户测试账号 B",
        "badge_text": "取件用户",
        "demo_group": "pickup_user",
        "clear_courier": True,
    },
    {
        "key": "user_c",
        "label": "用户C",
        "openid": "mock_user_c",
        "username": "用户C",
        "user_role": "user",
        "phone_number": "13800000004",
        "email": "userc@example.test",
        "receiver_name": "用户C",
        "audiences": ["miniapp"],
        "description": "取件用户测试账号 C",
        "badge_text": "取件用户",
        "demo_group": "pickup_user",
        "clear_courier": True,
    },
    {
        "key": "new_user",
        "label": "新用户注册测试",
        "openid": "mock_new_user",
        "username": "",
        "user_role": "user",
        "phone_number": "",
        "email": "",
        "audiences": ["miniapp"],
        "description": "首次注册身份选择测试",
        "badge_text": "新用户",
        "demo_group": "register",
        "clear_courier": True,
        "reset_profile": True,
    },
    {
        "key": "courier_a",
        "label": "配送员A",
        "openid": "mock_courier_a",
        "username": "配送员A",
        "courier_name": "配送员A",
        "nickname": "配送员A",
        "user_role": "courier",
        "phone_number": "13800000005",
        "email": "couriera@example.test",
        "courier_apply_status": "approved",
        "courier_status": "available",
        "courier_note": "配送员A已审核通过。",
        "audiences": ["miniapp"],
        "description": "已审核配送员测试账号 A",
        "badge_text": "已审核配送员",
        "demo_group": "courier",
    },
    {
        "key": "courier_b",
        "label": "配送员B",
        "openid": "mock_courier_b",
        "username": "配送员B",
        "courier_name": "配送员B",
        "nickname": "配送员B",
        "user_role": "courier",
        "phone_number": "13800000006",
        "email": "courierb@example.test",
        "courier_apply_status": "approved",
        "courier_status": "available",
        "courier_note": "配送员B已审核通过。",
        "audiences": ["miniapp"],
        "description": "已审核配送员测试账号 B",
        "badge_text": "已审核配送员",
        "demo_group": "courier",
    },
    {
        "key": "courier_c",
        "label": "配送员C",
        "openid": "mock_courier_c",
        "username": "配送员C",
        "courier_name": "配送员C",
        "nickname": "配送员C",
        "user_role": "courier",
        "phone_number": "13800000007",
        "email": "courierc@example.test",
        "courier_apply_status": "approved",
        "courier_status": "available",
        "courier_note": "配送员C已审核通过。",
        "audiences": ["miniapp"],
        "description": "已审核配送员测试账号 C",
        "badge_text": "已审核配送员",
        "demo_group": "courier",
    },
    {
        "key": "courier_d",
        "label": "配送员D",
        "openid": "mock_courier_d",
        "username": "配送员D",
        "courier_name": "配送员D",
        "nickname": "配送员D",
        "user_role": "courier",
        "phone_number": "13800000008",
        "email": "courierd@example.test",
        "courier_apply_status": "approved",
        "courier_status": "available",
        "courier_note": "配送员D已审核通过。",
        "audiences": ["miniapp"],
        "description": "已审核配送员测试账号 D",
        "badge_text": "已审核配送员",
        "demo_group": "courier",
    },
    {
        "key": "courier_e",
        "label": "配送员E",
        "openid": "mock_courier_e",
        "username": "配送员E",
        "courier_name": "配送员E",
        "nickname": "配送员E",
        "user_role": "courier",
        "phone_number": "13800000009",
        "email": "couriere@example.test",
        "courier_apply_status": "approved",
        "courier_status": "available",
        "courier_note": "配送员E已审核通过。",
        "audiences": ["miniapp"],
        "description": "已审核配送员测试账号 E",
        "badge_text": "已审核配送员",
        "demo_group": "courier",
    },
    {
        "key": "courier_pending",
        "label": "待审核配送员",
        "openid": "mock_courier_pending",
        "username": "待审核配送员",
        "courier_name": "待审核配送员",
        "nickname": "待审核配送员",
        "user_role": "courier",
        "phone_number": "13800000010",
        "email": "courierpending@example.test",
        "courier_apply_status": "pending",
        "courier_status": "available",
        "courier_note": "待审核配送员测试账号。",
        "audiences": ["miniapp"],
        "description": "待审核配送员测试账号",
        "badge_text": "待审核配送员",
        "demo_group": "review",
    },
    {
        "key": "courier_rejected",
        "label": "已驳回配送员",
        "openid": "mock_courier_rejected",
        "username": "已驳回配送员",
        "courier_name": "已驳回配送员",
        "nickname": "已驳回配送员",
        "user_role": "courier",
        "phone_number": "13800000011",
        "email": "courierrejected@example.test",
        "courier_apply_status": "rejected",
        "courier_status": "available",
        "courier_note": "资料未通过演示审核，可重新提交申请。",
        "audiences": ["miniapp"],
        "description": "已驳回配送员测试账号",
        "badge_text": "已驳回配送员",
        "demo_group": "review",
    },
    {
        "key": "admin",
        "label": "Admin",
        "openid": "mock_admin_main",
        "username": "admin",
        "display_name": "系统管理员",
        "user_role": "admin",
        "phone_number": "",
        "email": "",
        "audiences": ["admin_console"],
        "description": "Admin backend account",
        "badge_text": "Admin",
    },
]


def _fill_if_empty(obj, attr: str, value):
    if value in (None, ""):
        return False
    current = getattr(obj, attr, None)
    if current not in (None, ""):
        return False
    setattr(obj, attr, value)
    return True


def _identity_roles(identity: dict):
    roles = []
    user_role = (identity.get("user_role") or "user").strip()
    apply_status = (identity.get("courier_apply_status") or "").strip()
    if "miniapp" in (identity.get("audiences") or []) and user_role != "courier":
        roles.append("user")
    if apply_status == "approved":
        roles.append("courier")
    if user_role == "admin":
        roles.append("admin")
    return roles


def serialize_mock_identity(identity: dict):
    if not identity:
        return {}

    return {
        "key": identity["key"],
        "label": identity["label"],
        "openid": identity["openid"],
        "username": identity["username"],
        "courierName": identity.get("courier_name") or "",
        "nickname": identity.get("nickname") or "",
        "phoneNumber": identity["phone_number"],
        "userRole": identity.get("user_role") or "user",
        "courierApplyStatus": identity.get("courier_apply_status") or "",
        "courierStatus": identity.get("courier_status") or "",
        "demoGroup": identity.get("demo_group") or "",
        "roles": _identity_roles(identity),
        "audiences": list(identity.get("audiences") or []),
        "description": identity.get("description") or "",
        "badgeText": identity.get("badge_text") or "",
    }


def list_mock_identities(*, audience: str = ""):
    expected_audience = (audience or "").strip()
    items = []
    for identity in MOCK_IDENTITIES:
        audiences = identity.get("audiences") or []
        if expected_audience and expected_audience not in audiences:
            continue
        items.append(serialize_mock_identity(identity))
    return items


def get_mock_identity(identity_key_or_openid: str):
    value = (identity_key_or_openid or "").strip().lower()
    if not value:
        value = DEFAULT_MOCK_IDENTITY_KEY

    for identity in MOCK_IDENTITIES:
        if identity["key"] == value or identity["openid"].lower() == value:
            return dict(identity)
    return None


def _sync_user(identity: dict):
    if (identity.get("user_role") or "user").strip() == "courier":
        return None
    if (identity.get("user_role") or "user").strip() == "admin":
        return None

    user = User.query.filter_by(openid=identity["openid"]).first()
    if user:
        return user
    if identity.get("reset_profile"):
        return None

    user = User(openid=identity["openid"])
    db.session.add(user)
    user.role = identity.get("user_role") or "user"
    user.username = identity.get("username") or None
    user.phone_number = identity.get("phone_number") or None
    user.email = identity.get("email") or None
    user.receiver_name = identity.get("receiver_name") or None
    user.pickup_area = identity.get("pickup_area") or None
    user.backup_phone = identity.get("backup_phone") or None
    return user


def _sync_courier(identity: dict):
    openid = identity["openid"]
    apply_status = (identity.get("courier_apply_status") or "").strip()
    courier = Courier.query.filter_by(openid=openid).first()

    if not apply_status:
        return courier

    now = datetime.utcnow()
    if not courier:
        courier = Courier(
            openid=openid,
            applied_at=now,
            courier_name=identity.get("courier_name") or identity.get("username") or None,
            nickname=identity.get("nickname") or identity.get("courier_name") or identity.get("username") or None,
            phone_number=identity.get("phone_number") or None,
            status=identity.get("courier_status") or "available",
            apply_status=apply_status,
            reviewed_at=now if apply_status == "approved" else None,
            review_note=identity.get("courier_note") or None,
        )
        db.session.add(courier)
        return courier

    _fill_if_empty(courier, "courier_name", identity.get("courier_name") or identity.get("username"))
    _fill_if_empty(courier, "nickname", identity.get("nickname") or identity.get("courier_name") or identity.get("username"))
    _fill_if_empty(courier, "phone_number", identity.get("phone_number"))
    _fill_if_empty(courier, "status", identity.get("courier_status") or "available")
    _fill_if_empty(courier, "apply_status", apply_status)
    if not courier.applied_at:
        courier.applied_at = now
    if (courier.apply_status or "").strip() == "approved":
        courier.reviewed_at = courier.reviewed_at or now
    _fill_if_empty(courier, "review_note", identity.get("courier_note"))
    return courier


def _sync_admin(identity: dict):
    if (identity.get("user_role") or "").strip() != "admin":
        return None

    admin = Admin.query.filter_by(openid=identity["openid"]).first()
    if not admin:
        admin = Admin(
            openid=identity["openid"],
            username=identity.get("username") or "admin",
            display_name=identity.get("display_name") or identity.get("label") or identity.get("username") or "admin",
            role="admin",
            password_hash=identity.get("password_hash") or None,
        )
        db.session.add(admin)
        return admin

    _fill_if_empty(admin, "username", identity.get("username") or "admin")
    _fill_if_empty(admin, "display_name", identity.get("display_name") or identity.get("label") or admin.username)
    _fill_if_empty(admin, "role", "admin")
    _fill_if_empty(admin, "password_hash", identity.get("password_hash"))
    return admin


def ensure_mock_identities():
    for identity in MOCK_IDENTITIES:
        _sync_user(identity)
        _sync_courier(identity)
        _sync_admin(identity)

    db.session.commit()

    admin_identity = get_mock_identity("admin")
    return Admin.query.filter_by(openid=admin_identity["openid"]).first() if admin_identity else None


def ensure_mock_identity(identity_key_or_openid: str):
    identity = get_mock_identity(identity_key_or_openid)
    if not identity:
        return None

    before_new = set(db.session.new)
    before_dirty = set(db.session.dirty)
    synced = (
        _sync_user(identity)
        or _sync_courier(identity)
        or _sync_admin(identity)
    )
    has_changes = (
        any(obj not in before_new for obj in db.session.new)
        or any(obj not in before_dirty for obj in db.session.dirty)
    )
    if has_changes:
        db.session.commit()
    return synced
