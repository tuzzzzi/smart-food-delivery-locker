import requests
from flask import Blueprint, jsonify, request

from config import Config
from extensions import db
from models import Admin, Courier, User
from services.mock_identities import (
    DEFAULT_MOCK_IDENTITY_KEY,
    ensure_mock_identity,
    get_mock_identity,
    list_mock_identities,
    serialize_mock_identity,
)

auth_bp = Blueprint("auth", __name__)


def _wx_code2session(code: str) -> dict:
    url = (
        "https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={Config.APP_ID}&secret={Config.APP_SECRET}"
        f"&js_code={code}&grant_type=authorization_code"
    )
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, timeout=8)
    return resp.json()


def _upsert_user(openid: str) -> User:
    user = User.query.filter_by(openid=openid).first()
    if not user:
        user = User(openid=openid, role="user")
        db.session.add(user)
        db.session.commit()
    return user


def ensure_mock_admin_user():
    if not Config.USE_MOCK:
        return None
    return ensure_mock_identity("admin")


def _build_roles(openid: str):
    roles = []
    user = User.query.filter_by(openid=openid).first()
    courier = Courier.query.filter_by(openid=openid).first()
    admin = Admin.query.filter_by(openid=openid).first()

    if admin and (admin.role or "").strip() == "admin":
        roles.append("admin")

    if courier and (courier.apply_status or "").strip() == "approved":
        roles.append("courier")

    if user:
        roles.append("user")

    return roles


def _courier_apply_status(openid: str) -> str:
    courier = Courier.query.filter_by(openid=openid).first()
    return (courier.apply_status or "none").strip() if courier else "none"


def _needs_profile(user: User) -> bool:
    if not user:
        return False

    if user and (user.role or "").strip() == "courier":
        return not bool(
            (user.username or "").strip()
            and (user.phone_number or "").strip()
        )

    return not bool(
        user
        and (user.username or "").strip()
        and (user.phone_number or "").strip()
        and (user.receiver_name or "").strip()
    )


@auth_bp.get("/api/mock/identities")
def mock_identities():
    if not Config.USE_MOCK:
        return jsonify({"status": "error", "message": "Mock mode disabled"}), 404

    audience = (request.args.get("audience") or "").strip()
    return jsonify({
        "status": "success",
        "defaultIdentityKey": DEFAULT_MOCK_IDENTITY_KEY,
        "identities": list_mock_identities(audience=audience),
    })


@auth_bp.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}

    if Config.USE_MOCK:
        identity_key = (
            data.get("mockIdentityKey")
            or data.get("mockIdentity")
            or data.get("identityKey")
            or DEFAULT_MOCK_IDENTITY_KEY
        )
        identity = get_mock_identity(identity_key)
        if not identity:
            return jsonify({"status": "error", "message": "Invalid mock identity"}), 400

        ensure_mock_identity(identity_key)
        openid = identity["openid"]
        identity_role = (identity.get("user_role") or "user").strip()
        user = User.query.filter_by(openid=openid).first()
        token = openid
        roles = _build_roles(openid)
        courier_apply_status = _courier_apply_status(openid)
        need_profile = (
            False
            if identity_role in {"courier", "admin"} or courier_apply_status in {"pending", "approved", "rejected"}
            else (not roles or _needs_profile(user))
        )

        return jsonify({
            "status": "success",
            "openid": openid,
            "token": token,
            "roles": roles,
            "needProfile": need_profile,
            "courierApplyStatus": courier_apply_status,
            "identity": serialize_mock_identity(identity),
        })

    code = data.get("code")
    if not code:
        return jsonify({"status": "error", "message": "Missing login code"}), 400

    result = _wx_code2session(code)
    if "errcode" in result and result.get("errcode") not in (0, None):
        return jsonify({"status": "error", "message": result.get("errmsg", "wx login error"), "wx": result}), 400

    openid = result.get("openid")
    if not openid:
        return jsonify({"status": "error", "message": "wx login failed: missing openid", "wx": result}), 400

    user = User.query.filter_by(openid=openid).first()
    courier = Courier.query.filter_by(openid=openid).first()
    admin = Admin.query.filter_by(openid=openid).first()
    token = openid
    roles = _build_roles(openid)
    courier_apply_status = _courier_apply_status(openid)
    need_profile = False if courier or admin else (not roles or _needs_profile(user))

    return jsonify({
        "status": "success",
        "openid": openid,
        "token": token,
        "roles": roles,
        "needProfile": need_profile,
        "courierApplyStatus": courier_apply_status,
    })
