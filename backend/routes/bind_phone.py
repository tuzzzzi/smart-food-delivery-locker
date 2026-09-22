import re

from flask import Blueprint, g, jsonify, request
from extensions import db
from models import User
from routes.guards import user_api_required

bind_bp = Blueprint("bind_bp", __name__)

def ok(**kwargs):
    payload = {"status": "success"}
    payload.update(kwargs)
    return jsonify(payload), 200

def err(message, code=400):
    return jsonify({"status": "error", "message": message}), code


@bind_bp.post("/api/user/bind_phone")
@user_api_required
def bind_phone():
    data = request.get_json(silent=True) or {}
    openid = (getattr(getattr(g, "current_user", None), "openid", "") or "").strip()
    phone = (data.get("phoneNumber") or "").strip()
    if not phone or not re.fullmatch(r"\d{11}", phone):
        return err("Phone number must be 11 digits", 400)

    user = User.query.filter_by(openid=openid).first()
    if not user:
        user = User(openid=openid, role="user")
        db.session.add(user)

    # ✅ 手机号唯一
    other = User.query.filter(User.phone_number == phone, User.openid != openid).first()
    if other:
        return err("该手机号/学号已被其他用户绑定", 409)

    user.phone_number = phone
    db.session.commit()

    return ok(message="绑定成功", phoneNumber=user.phone_number)
