import hashlib
import re
from datetime import datetime

import requests
from flask import Blueprint, current_app, g, jsonify, request

from config import Config
from extensions import db
from models import Courier, User
from routes.guards import get_request_token, user_api_required
from services.sms_verification import consume_sms_code, send_sms_code

profile_bp = Blueprint("profile", __name__)


def _current_user():
    return getattr(g, "current_user", None)


def _current_user_openid() -> str:
    return (getattr(_current_user(), "openid", "") or "").strip()


def _request_openid() -> str:
    return _current_user_openid() or get_request_token()


def _ensure_phone_available(phone_number: str, current_openid: str):
    value = (phone_number or "").strip()
    if not value:
        return None

    other = User.query.filter(User.phone_number == value, User.openid != current_openid).first()
    if other:
        return jsonify({"status": "error", "message": "Phone number already bound to another user"}), 409
    return None


def _ensure_courier_phone_available(phone_number: str, current_openid: str):
    value = (phone_number or "").strip()
    if not value:
        return None

    other = Courier.query.filter(Courier.phone_number == value, Courier.openid != current_openid).first()
    if other:
        return jsonify({"status": "error", "message": "Phone number already used by another courier"}), 409
    return None


def _normalize_profile_role(raw_role: str) -> str:
    role = (raw_role or "").strip().lower()
    return role if role in {"user", "courier"} else ""


def _sync_courier_registration(
    user: User,
    phone_number: str,
    courier_name: str = "",
    nickname: str = "",
) -> bool:
    now = datetime.utcnow()
    changed = False
    courier = Courier.query.filter_by(openid=user.openid).first()
    courier_name = (courier_name or "").strip()
    nickname = (nickname or "").strip() or courier_name

    if not courier:
        courier = Courier(
            openid=user.openid,
            courier_name=courier_name or None,
            nickname=nickname or None,
            phone_number=phone_number,
            status="available",
            apply_status="pending",
            applied_at=now,
            created_at=now,
            updated_at=now,
        )
        db.session.add(courier)
        return True

    if courier_name and courier_name != (courier.courier_name or ""):
        courier.courier_name = courier_name
        changed = True
    if nickname and nickname != (courier.nickname or ""):
        courier.nickname = nickname
        changed = True
    if phone_number and phone_number != (courier.phone_number or ""):
        courier.phone_number = phone_number
        changed = True
    if not courier.status:
        courier.status = "available"
        changed = True
    if (courier.apply_status or "").strip() != "approved":
        if courier.apply_status != "pending":
            courier.apply_status = "pending"
            changed = True
        if not courier.applied_at:
            courier.applied_at = now
            changed = True
    courier.updated_at = now
    return True if changed else False


def _set_user_role_from_profile(
    user: User,
    selected_role: str,
    phone_number: str,
    courier_name: str = "",
    nickname: str = "",
) -> bool:
    if not selected_role or (user.role or "").strip() == "admin":
        return False

    changed = False
    target_user_role = "user" if selected_role == "courier" else selected_role
    if target_user_role != (user.role or "").strip():
        user.role = target_user_role
        changed = True

    if selected_role == "courier":
        changed = _sync_courier_registration(user, phone_number, courier_name, nickname) or changed

    return changed


def _profile_payload(user: User):
    courier = Courier.query.filter_by(openid=user.openid).first() if user and user.openid else None
    apply_status = (courier.apply_status or "none").strip() if courier else "none"
    roles = ["user"]
    if courier and apply_status == "approved":
        roles.append("courier")
    if user and (user.role or "").strip() == "admin":
        roles.append("admin")

    return {
        "user": user.to_dict() if user else None,
        "roles": roles,
        "apply_status": apply_status,
        "applyStatus": apply_status,
        "courier": courier.to_dict() if courier else None,
    }


def _mock_phone_for_openid(openid: str) -> str:
    value = (openid or "").strip()
    digest = hashlib.md5(value.encode("utf-8")).hexdigest() if value else "0" * 32
    digits = "".join(ch for ch in digest if ch.isdigit()) or "1234567890"
    tail = (digits * 2)[:8]
    return f"138{tail}"


def _get_access_token() -> str:
    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={Config.APP_ID}&secret={Config.APP_SECRET}"
    )
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, timeout=8)
    data = resp.json()
    if data.get("errcode"):
        raise RuntimeError(f"get_access_token failed: {data}")
    return data.get("access_token")


@profile_bp.route("/api/profile/me", methods=["GET", "POST"])
@user_api_required
def me():
    user = _current_user()
    return jsonify({"status": "success", "user": user.to_dict() if user else None})


@profile_bp.post("/api/profile/phone")
def phone():
    openid = _request_openid()
    data = request.get_json(silent=True) or {}
    phone_code = data.get("phoneCode")

    if not openid:
        return jsonify({"status": "error", "message": "Missing token"}), 401

    if Config.USE_MOCK:
        user = User.query.filter_by(openid=openid).first()
        phone_number = (getattr(user, "phone_number", "") or "").strip() or _mock_phone_for_openid(openid)
        conflict = _ensure_phone_available(phone_number, openid)
        if conflict:
            return conflict

        if user:
            user.phone_number = phone_number
            db.session.commit()
        return jsonify({"status": "success", "phoneNumber": phone_number})

    if not phone_code:
        return jsonify({"status": "error", "message": "Missing phoneCode"}), 400

    try:
        access_token = _get_access_token()
    except Exception as exc:
        return jsonify({"status": "error", "message": f"get access_token failed: {exc}"}), 500

    url = f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={access_token}"
    session = requests.Session()
    session.trust_env = False
    resp = session.post(url, json={"code": phone_code}, timeout=8)
    wx_data = resp.json()

    if wx_data.get("errcode"):
        return jsonify({"status": "error", "message": wx_data.get("errmsg", "wx phone error"), "wx": wx_data}), 400

    phone_info = wx_data.get("phone_info") or {}
    phone_number = (phone_info.get("phoneNumber") or "").strip()
    if not phone_number:
        return jsonify({"status": "error", "message": "wx phone success but missing phoneNumber", "wx": wx_data}), 400

    conflict = _ensure_phone_available(phone_number, openid)
    if conflict:
        return conflict

    user = User.query.filter_by(openid=openid).first()
    if user:
        user.phone_number = phone_number
        db.session.commit()

    return jsonify({"status": "success", "phoneNumber": phone_number})


@profile_bp.post("/api/profile/send_code")
def send_profile_code():
    openid = _request_openid()
    data = request.get_json(silent=True) or {}
    phone_number = (data.get("phoneNumber") or data.get("phone_number") or "").strip()

    if not openid:
        return jsonify({"status": "error", "message": "Missing token"}), 401

    conflict = _ensure_phone_available(phone_number, openid)
    if conflict:
        return conflict

    ok, message, http, payload = send_sms_code(openid, phone_number)
    if not ok:
        return jsonify({"status": "error", "message": message}), http

    return jsonify({"status": "success", "message": message, **payload})


@profile_bp.post("/api/profile/verify_code")
def verify_profile_code():
    openid = _request_openid()
    data = request.get_json(silent=True) or {}
    phone_number = (data.get("phoneNumber") or data.get("phone_number") or "").strip()
    code = (data.get("code") or "").strip()

    if not openid:
        return jsonify({"status": "error", "message": "Missing token"}), 401

    conflict = _ensure_phone_available(phone_number, openid)
    if conflict:
        return conflict

    ok, message, http = consume_sms_code(openid, phone_number, code)
    if not ok:
        return jsonify({"status": "error", "message": message}), http

    return jsonify({"status": "success", "message": message})


@profile_bp.post("/api/profile/complete")
def complete():
    openid = _request_openid()
    data = request.get_json(silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    courier_name = (data.get("courierName") or data.get("courier_name") or data.get("username") or "").strip()
    phone_number = (data.get("phoneNumber") or "").strip()
    email = (data.get("email") or "").strip()
    receiver_name = (data.get("receiverName") or data.get("receiver_name") or "").strip()
    pickup_area = (data.get("pickupArea") or data.get("pickup_area") or "").strip()
    backup_phone = (data.get("backupPhone") or data.get("backup_phone") or "").strip()
    selected_role = _normalize_profile_role(data.get("role") or data.get("selectedRole"))

    if not openid:
        return jsonify({"status": "error", "message": "Missing token"}), 401
    if selected_role != "user":
        return jsonify({"status": "error", "message": "Courier registration must use courier apply API"}), 400

    display_name = nickname or courier_name
    if not display_name:
        return jsonify({"status": "error", "message": "Nickname is required"}), 400
    if not phone_number:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400
    if not re.fullmatch(r"\d{11}", phone_number):
        return jsonify({"status": "error", "message": "Phone number must be 11 digits"}), 400
    if backup_phone and not re.fullmatch(r"\d{11}", backup_phone):
        return jsonify({"status": "error", "message": "Backup phone must be 11 digits"}), 400
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"status": "error", "message": "Email format is invalid"}), 400

    user = User.query.filter_by(openid=openid).first()
    if not user:
        user = User(openid=openid, role="user")
        db.session.add(user)

    conflict = _ensure_phone_available(phone_number, openid)
    if conflict:
        return conflict

    user.username = display_name
    user.role = "user"
    user.phone_number = phone_number
    if email:
        user.email = email
    user.receiver_name = receiver_name or None
    user.pickup_area = pickup_area or None
    user.backup_phone = backup_phone or None

    db.session.commit()
    return jsonify({"status": "success", **_profile_payload(user)})


@profile_bp.post("/api/user/profile/update")
@user_api_required
def update_profile():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    courier_name = data.get("courierName", data.get("courier_name"))
    nickname = data.get("nickname")
    phone_number = data.get("phoneNumber", data.get("phone_number"))
    email = data.get("email")
    receiver_name = data.get("receiverName", data.get("receiver_name"))
    pickup_area = data.get("pickupArea", data.get("pickup_area"))
    backup_phone = data.get("backupPhone", data.get("backup_phone"))
    work_status = data.get("workStatus")
    selected_role = _normalize_profile_role(data.get("role") or data.get("selectedRole"))
    openid = _current_user_openid()
    current_app.logger.info("[profile.update] openid=%s payload=%s", openid, data)

    user = User.query.filter_by(openid=openid).first()
    if not user:
        current_app.logger.warning("[profile.update] user not found openid=%s", openid)
        return jsonify({"status": "error", "message": "User not found"}), 404

    changed = False

    if username is not None:
        username = str(username).strip()
        if not username:
            return jsonify({"status": "error", "message": "Username is required"}), 400
        if username != (user.username or ""):
            user.username = username
            changed = True

    if phone_number is not None:
        phone_number = str(phone_number).strip()
        if phone_number:
            if not re.fullmatch(r"\d{11}", phone_number):
                return jsonify({"status": "error", "message": "Phone number must be 11 digits"}), 400

            if phone_number != (user.phone_number or ""):
                conflict = _ensure_phone_available(phone_number, openid)
                if conflict:
                    return conflict
                if selected_role == "courier":
                    conflict = _ensure_courier_phone_available(phone_number, openid)
                    if conflict:
                        return conflict
                user.phone_number = phone_number
                changed = True

    if email is not None:
        email = str(email).strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return jsonify({"status": "error", "message": "Email format is invalid"}), 400
        if email != (user.email or ""):
            user.email = email or None
            changed = True

    if receiver_name is not None:
        receiver_name = str(receiver_name).strip()
        if receiver_name != (user.receiver_name or ""):
            user.receiver_name = receiver_name or None
            changed = True

    if pickup_area is not None:
        pickup_area = str(pickup_area).strip()
        if pickup_area != (user.pickup_area or ""):
            user.pickup_area = pickup_area or None
            changed = True

    if backup_phone is not None:
        backup_phone = str(backup_phone).strip()
        if backup_phone and not re.fullmatch(r"\d{11}", backup_phone):
            return jsonify({"status": "error", "message": "Backup phone must be 11 digits"}), 400
        if backup_phone != (user.backup_phone or ""):
            user.backup_phone = backup_phone or None
            changed = True

    if work_status is not None:
        work_status = str(work_status).strip() or "空闲"
        if work_status not in {"空闲", "配送中", "暂停接单"}:
            return jsonify({"status": "error", "message": "Invalid work status"}), 400
        courier = Courier.query.filter_by(openid=openid).first()
        if courier and work_status != (courier.work_status or "空闲"):
            courier.work_status = work_status
            changed = True

    effective_phone = (phone_number or user.phone_number or "").strip()
    if selected_role == "courier":
        if not effective_phone:
            return jsonify({"status": "error", "message": "Phone number is required for courier registration"}), 400
        conflict = _ensure_courier_phone_available(effective_phone, openid)
        if conflict:
            return conflict

    if selected_role:
        courier_name_value = (
            str(courier_name).strip()
            if courier_name is not None
            else (str(username).strip() if selected_role == "courier" and username is not None else "")
        )
        nickname_value = str(nickname).strip() if nickname is not None else courier_name_value
        changed = _set_user_role_from_profile(
            user,
            selected_role,
            effective_phone,
            courier_name_value,
            nickname_value,
        ) or changed

    db.session.commit()
    saved_user = User.query.filter_by(openid=openid).first()
    current_app.logger.info(
        "[profile.update] committed openid=%s user=%s",
        openid,
        saved_user.to_dict() if saved_user else None,
    )
    payload = _profile_payload(saved_user)
    if not changed:
        return jsonify({"status": "success", "message": "No changes", **payload}), 200
    return jsonify({"status": "success", **payload}), 200
