from functools import wraps

from flask import g, jsonify, request, session

from models import Admin, Courier, User

def _request_json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def get_request_value(*names):
    for name in names:
        value = request.args.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()

    for name in names:
        value = request.form.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()

    data = _request_json()
    for name in names:
        value = data.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def get_request_token():
    token = (request.headers.get("Authorization") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token:
        return token

    token = get_request_value("token")
    if token:
        return token

    return ""


def resolve_user_by_token(token: str):
    token = (token or "").strip()
    if not token:
        return None
    return User.query.filter_by(openid=token).first()


def user_is_admin(user: User) -> bool:
    return bool(user and (user.role or "").strip() == "admin")


def admin_is_admin(admin: Admin) -> bool:
    return bool(admin and (admin.role or "").strip() == "admin")


def resolve_admin_by_token(token: str):
    token = (token or "").strip()
    if not token:
        return None
    return Admin.query.filter_by(openid=token).first()


def get_approved_courier(openid: str):
    openid = (openid or "").strip()
    if not openid:
        return None
    return Courier.query.filter_by(openid=openid, apply_status="approved").first()


def _set_request_identity(*, user: User = None, courier: Courier = None, admin: Admin = None):
    if user:
        g.current_user = user
        g.current_openid = user.openid
    if courier:
        g.current_courier = courier
        if not getattr(g, "current_openid", None):
            g.current_openid = courier.openid
    if admin:
        g.current_admin = admin


def get_admin_session_user():
    admin_openid = (session.get("admin_openid") or "").strip()
    if not admin_openid:
        return None

    admin = resolve_admin_by_token(admin_openid)
    if not admin_is_admin(admin):
        session.pop("admin_openid", None)
        return None

    _set_request_identity(admin=admin)
    return admin


def store_admin_session(admin: Admin):
    if admin and admin.openid:
        session["admin_openid"] = admin.openid


def clear_admin_session():
    session.pop("admin_openid", None)


def authenticate_admin_token(token: str, *, persist_session: bool = True):
    admin = resolve_admin_by_token(token)
    if not admin_is_admin(admin):
        return None

    _set_request_identity(admin=admin)
    if persist_session:
        store_admin_session(admin)
    return admin


def authenticate_admin_request(*, persist_session: bool = True):
    user = get_admin_session_user()
    if user:
        return user

    token = get_request_token()
    if not token:
        return None

    return authenticate_admin_token(token, persist_session=persist_session)


def authenticate_user_token(token: str):
    user = resolve_user_by_token(token)
    if not user:
        return None

    _set_request_identity(user=user)
    return user


def authenticate_user_request():
    token = get_request_token()
    if not token:
        return None
    return authenticate_user_token(token)


def _courier_auth_error(message: str, code: int):
    return jsonify({"status": "error", "message": message}), code


def _user_auth_error(message: str, code: int):
    return jsonify({"status": "error", "message": message}), code


def user_api_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_request_token()
        if not token:
            return _user_auth_error("Missing user token", 401)

        user = authenticate_user_token(token)
        if not user:
            return _user_auth_error("Invalid token", 401)

        declared_openid = get_request_value("openid")
        if declared_openid and declared_openid != user.openid:
            return _user_auth_error("Forbidden user identity", 403)

        return fn(*args, **kwargs)

    return wrapper


def courier_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_request_token()
        if not token:
            return _courier_auth_error("Missing token", 401)
        # ✅开发阶段 token=openid，这里继续你的逻辑即可
        # TODO: 校验token是否为 courier 身份
        courier = get_approved_courier(token)
        if not courier:
            return _courier_auth_error("Current user is not an approved courier", 403)

        declared_openid = get_request_value("courierOpenid", "courier_openid")
        if declared_openid and declared_openid != courier.openid:
            return _courier_auth_error("Forbidden courier identity", 403)

        _set_request_identity(courier=courier)
        return fn(*args, **kwargs)
    return wrapper


def admin_api_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = authenticate_admin_request(persist_session=False)
        if not user:
            token = get_request_token()
            if not token:
                return jsonify({"status": "error", "message": "Missing admin token"}), 401

            admin_candidate = resolve_admin_by_token(token)
            if not admin_candidate:
                return jsonify({"status": "error", "message": "Invalid token"}), 401

            return jsonify({"status": "error", "message": "Admin permission required"}), 403

        return fn(*args, **kwargs)

    return wrapper
