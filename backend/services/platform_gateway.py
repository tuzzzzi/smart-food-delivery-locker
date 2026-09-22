import json
import random
import time

from extensions import db
from models import PlatformOrder
from services.audit_log import log_action
from services.integration_runtime import get_platform_integration_config
from services.order_qr import ensure_order_qr
from services.ownership import PHONE_MATCHED, match_user_by_phone, phone_match_http_code


class PlatformPayloadError(ValueError):
    def __init__(self, message: str, *, reason_code: str = "", http_code: int = 400):
        super().__init__(message)
        self.reason_code = (reason_code or "").strip()
        self.http_code = int(http_code or 400)


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}-{random.randint(100, 999)}"


def pick_payload_value(payload, *keys, default=""):
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def verify_platform_request_signature(req):
    config = get_platform_integration_config()
    secret = (config.get("pushSecretConfigured") and current_platform_secret()) or ""
    if config["mode"] == "mock" or not secret:
        return True, ""

    signature = (req.headers.get("X-Platform-Signature") or "").strip()
    if not signature:
        return False, "Missing X-Platform-Signature"
    if signature != secret:
        return False, "Invalid platform signature"
    return True, ""


def current_platform_secret() -> str:
    from flask import current_app

    return (current_app.config.get("PLATFORM_PUSH_SECRET") or "").strip()


def normalize_platform_payload(payload, *, source: str, provider_name: str = ""):
    platform_name = pick_payload_value(payload, "platformName", "platform_name", default=provider_name or "generic")
    external_order_no = pick_payload_value(payload, "externalOrderNo", "external_order_no")
    receiver_phone = pick_payload_value(payload, "receiverPhone", "receiver_phone")
    receiver_openid = pick_payload_value(payload, "receiverOpenid", "receiver_openid")
    receiver_name = pick_payload_value(payload, "receiverName", "receiver_name")
    receiver_address = pick_payload_value(payload, "receiverAddress", "receiver_address")
    merchant_name = pick_payload_value(payload, "merchantName", "merchant_name")
    merchant_note = pick_payload_value(payload, "merchantNote", "merchant_note")

    if not external_order_no:
        external_order_no = _gen_id("EXT")

    return {
        "platform_name": platform_name or "generic",
        "external_order_no": external_order_no,
        "receiver_phone": receiver_phone,
        "receiver_openid": receiver_openid,
        "receiver_name": receiver_name,
        "receiver_address": receiver_address,
        "merchant_name": merchant_name,
        "merchant_note": merchant_note,
        "source": source or "platform_webhook",
        "raw_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def upsert_platform_order_from_payload(
    payload,
    *,
    source: str,
    provider_name: str = "",
    actor_type: str = "platform",
    actor_openid: str = "platform_webhook",
):
    normalized = normalize_platform_payload(payload, source=source, provider_name=provider_name)
    if not normalized["receiver_phone"]:
        raise PlatformPayloadError("缺少收件人手机号", reason_code="missing_phone", http_code=400)

    phone_match = match_user_by_phone(normalized["receiver_phone"])
    if phone_match_http_code(phone_match) == 400 and phone_match["status"] != PHONE_MATCHED:
        raise PlatformPayloadError(
            phone_match["message"] or "手机号格式不正确",
            reason_code=phone_match.get("reasonCode") or "",
            http_code=400,
        )

    normalized["receiverMatchStatus"] = phone_match["status"]
    normalized["receiverMatchReasonCode"] = phone_match.get("reasonCode") or ""
    normalized["receiverMatchMessage"] = phone_match.get("message") or ""

    resolved_receiver_openid = phone_match["openid"] if phone_match["status"] == PHONE_MATCHED else ""
    receiver_match_note = (phone_match["message"] or "").strip() if phone_match["status"] != PHONE_MATCHED else ""
    declared_receiver_openid = (normalized["receiver_openid"] or "").strip()
    if declared_receiver_openid and declared_receiver_openid != resolved_receiver_openid:
        raise PlatformPayloadError(
            "receiverOpenid 与 receiverPhone 匹配到的用户不一致",
            reason_code="receiver_openid_mismatch",
            http_code=409,
        )

    order = PlatformOrder.query.filter_by(
        platform_name=normalized["platform_name"],
        external_order_no=normalized["external_order_no"],
    ).first()

    created = False
    if not order:
        order = PlatformOrder(
            platform_order_id=_gen_id("PO"),
            platform_name=normalized["platform_name"],
            external_order_no=normalized["external_order_no"],
            source=normalized["source"],
            status="pending_dispatch",
            receiver_name=normalized["receiver_name"],
            receiver_phone=normalized["receiver_phone"],
            receiver_address=normalized["receiver_address"],
            receiver_openid=resolved_receiver_openid or None,
            merchant_name=normalized["merchant_name"],
            merchant_note=normalized["merchant_note"],
            raw_payload=normalized["raw_payload"],
            status_note=(receiver_match_note[:255] if receiver_match_note else None),
        )
        db.session.add(order)
        ensure_order_qr(order)
        log_action(
            "platform_order.created",
            actor_type=actor_type,
            actor_openid=actor_openid,
            platform_order_id=order.platform_order_id,
            detail=f"平台接口接收到订单：{order.platform_name} / {order.external_order_no}",
        )
        created = True
    else:
        order.source = normalized["source"]
        order.receiver_name = normalized["receiver_name"] or order.receiver_name
        order.receiver_phone = normalized["receiver_phone"]
        order.receiver_address = normalized["receiver_address"] or order.receiver_address
        order.receiver_openid = resolved_receiver_openid or None
        order.merchant_name = normalized["merchant_name"] or order.merchant_name
        order.merchant_note = normalized["merchant_note"] or order.merchant_note
        order.raw_payload = normalized["raw_payload"]
        order.status_note = receiver_match_note[:255] if receiver_match_note else None

    return order, created, normalized


def emit_platform_event(event_name: str, order=None, task=None, pkg=None):
    config = get_platform_integration_config()
    callback_url = (config.get("callbackUrl") or "").strip()
    mode = config["mode"]
    simulated = mode == "mock" or not callback_url

    note = "当前仅记录平台事件，未真实回调外卖平台。"
    if mode != "mock" and callback_url:
        note = "已预留真实平台回调地址，后续可在此替换为真实 HTTP 回调。"

    return {
        "event": event_name,
        "mode": mode,
        "providerName": config["providerName"],
        "callbackUrl": callback_url,
        "simulated": simulated,
        "note": note,
        "platformOrderId": getattr(order, "platform_order_id", None),
        "taskId": getattr(task, "task_id", None),
        "packageId": getattr(pkg, "package_id", None),
    }
