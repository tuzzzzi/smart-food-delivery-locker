import re
from flask import Blueprint, request, jsonify
from extensions import db
from models import User, Courier
from datetime import datetime
from services.sms_verification import consume_sms_code, send_sms_code

courier_apply_bp = Blueprint("courier_apply", __name__)

def _get_openid_from_json():
    data = request.get_json(silent=True) or {}
    return (data.get("token") or "").strip()

def _get_openid_from_args():
    return (request.args.get("openid") or request.args.get("token") or "").strip()

def _get_phone_from_json():
    data = request.get_json(silent=True) or {}
    return (data.get("phoneNumber") or data.get("phone_number") or "").strip()


def _phone_conflict(phone: str, openid: str):
    other_user = User.query.filter(User.phone_number == phone, User.openid != openid).first()
    if other_user:
        return "该手机号已绑定其他用户"

    other_courier = Courier.query.filter(Courier.phone_number == phone, Courier.openid != openid).first()
    if other_courier:
        return "该手机号已被其他配送员账号占用"

    return ""

def _ok(payload=None, message="ok"):
    d = {"status": "success", "message": message}
    if payload:
        d.update(payload)
    return jsonify(d)

def _err(message="error", http=200):
    return jsonify({"status": "error", "message": message}), http


@courier_apply_bp.post("/api/courier/apply/send_code")
def send_code():
    openid = _get_openid_from_json()
    phone = _get_phone_from_json()

    if not openid:
        return _err("Missing token", 401)
    if not phone or not re.fullmatch(r"\d{11}", phone):
        return _err("手机号必须为 11 位数字", 400)

    # ✅如果已经 approved，可以不允许再发
    existing = Courier.query.filter_by(openid=openid).first()
    if existing and existing.apply_status == "approved":
        return _err("你已是配送员，无需申请", 400)

    conflict = _phone_conflict(phone, openid)
    if conflict:
        return _err(conflict, 409)

    ok, message, http, payload = send_sms_code(openid, phone)
    if not ok:
        return _err(message, http)
    return _ok(payload, "验证码已发送（模拟）")


@courier_apply_bp.post("/api/courier/apply/verify")
def verify():
    """
    ✅短信验证通过 + 写入 Courier(待审核 pending)
    ⚠️不再直接给 user.role 加 courier，必须管理员审核通过后登录才有 courier 身份
    """
    openid = _get_openid_from_json()
    data = request.get_json(silent=True) or {}
    phone = (data.get("phoneNumber") or data.get("phone_number") or "").strip()
    code = (data.get("code") or "").strip()
    courier_name = (
        data.get("courierName")
        or data.get("courier_name")
        or data.get("username")
        or data.get("name")
        or ""
    ).strip()
    nickname = (data.get("nickname") or courier_name).strip()

    if not openid:
        return _err("Missing token", 401)
    if not phone or not code:
        return _err("缺少手机号或验证码", 400)
    if not re.fullmatch(r"\d{11}", phone):
        return _err("手机号必须为 11 位数字", 400)
    if len(code) != 6:
        return _err("请输入6位验证码", 400)

    ok, message, http = consume_sms_code(openid, phone, code)
    if not ok:
        return _err(message, http)

    conflict = _phone_conflict(phone, openid)
    if conflict:
        return _err(conflict, 409)

    now = datetime.utcnow()
    try:
        user = User.query.filter_by(openid=openid).first()
        courier = Courier.query.filter_by(openid=openid).first()
        if not courier:
            courier = Courier(
                openid=openid,
                courier_name=courier_name or None,
                nickname=nickname or None,
                phone_number=phone,
                status="available",
                apply_status="pending",
                created_at=now,
                applied_at=now,
                updated_at=now,
            )
            db.session.add(courier)
        else:
            if courier_name:
                courier.courier_name = courier_name
            if nickname:
                courier.nickname = nickname
            courier.phone_number = phone
            courier.status = courier.status or "available"
            if courier.apply_status != "approved":
                courier.apply_status = "pending"
                courier.applied_at = now
            courier.updated_at = now

        if user:
            user_role = (user.role or "").strip()
            has_user_profile = any(
                (value or "").strip()
                for value in (
                    user.username,
                    user.phone_number,
                    user.email,
                    user.receiver_name,
                    user.pickup_area,
                    user.backup_phone,
                )
            )
            if user_role == "courier" or not has_user_profile:
                db.session.delete(user)
                user = None

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[COURIER APPLY] verify failed openid={openid} phone={phone} error={exc}")
        return _err("提交申请失败，请稍后重试", 500)

    return _ok(
        {
            "apply_status": courier.apply_status,
            "courier_id": courier.courier_id,
            "openid": courier.openid,
            "courier_name": courier.courier_name,
            "courierName": courier.courier_name,
            "nickname": courier.nickname,
            "phone_number": courier.phone_number,
            "roles": ["user"] if user else [],
        },
        "验证码通过，已提交申请，等待管理员审核"
    )


@courier_apply_bp.get("/api/courier/status")
def courier_status():
    openid = (request.args.get("openid") or request.args.get("token") or "").strip()

    if not openid:
        return jsonify({
            "status": "error",
            "message": "Missing openid"
        }), 400

    c = Courier.query.filter_by(openid=openid).first()

    # 没申请过
    if not c:
        return jsonify({
            "status": "success",
            "apply_status": "none",
            "message": "未提交申请"
        })

    return jsonify({
        "status": "success",
        "apply_status": c.apply_status,
        "courier_name": c.courier_name,
        "courierName": c.courier_name,
        "nickname": c.nickname,
        "phone_number": c.phone_number,
        "work_status": c.work_status or "空闲",
        "review_note": c.review_note,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "status_text": "ok"
    })


@courier_apply_bp.post("/api/courier/apply/cancel")
def cancel_apply():
    """
    ✅可选：撤销申请（仅 pending 可撤销）
    """
    openid = _get_openid_from_json()
    if not openid:
        return _err("Missing token", 401)

    c = Courier.query.filter_by(openid=openid).first()
    if not c:
        return _err("未找到申请记录", 404)

    if c.apply_status != "pending":
        return _err("当前状态不可撤销", 400)

    # 撤销方式：直接删除记录 or 改成 rejected/none
    # 我建议：改成 rejected，并写备注（如果有字段）
    c.apply_status = "rejected"
    if hasattr(c, "review_note"):
        c.review_note = "用户撤销申请"
    if hasattr(c, "reviewed_at"):
        c.reviewed_at = datetime.utcnow()

    db.session.commit()
    return _ok({"apply_status": c.apply_status}, "已撤销申请")
