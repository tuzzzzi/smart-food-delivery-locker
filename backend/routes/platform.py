from flask import Blueprint, jsonify, request

from extensions import db
from models import Package, PlatformOrder
from routes.guards import authenticate_admin_request, get_request_token, resolve_admin_by_token
from services.audit_log import log_action
from services.platform_flow import (
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_DISPATCHED,
    ORDER_STATUS_EXCEPTION,
    ORDER_STATUS_READY_FOR_PICKUP,
    ORDER_STATUS_WAITING_AI,
    dispatch_order_to_task,
    emit_hardware_event,
    emit_platform_event,
    get_latest_task_for_order,
    get_order_status_view,
    get_task_stage,
)
from services.platform_gateway import PlatformPayloadError, upsert_platform_order_from_payload

platform_bp = Blueprint("platform", __name__)


def _build_order_view(order: PlatformOrder):
    task = get_latest_task_for_order(order.platform_order_id)
    pkg = Package.query.get(task.package_id) if task and task.package_id else None

    item = order.to_dict()
    item["task"] = task.to_dict() if task else None
    item["package"] = pkg.to_dict() if pkg else None
    item["statusView"] = get_order_status_view(order, task, pkg)
    item["taskStage"] = get_task_stage(task) if task else None
    return item


def _qr_status_text(order: PlatformOrder, task=None, pkg=None):
    if pkg and pkg.status == "picked":
        return "已完成取件"
    if pkg and pkg.status == "pending":
        return "已入柜，等待取件"
    if order.status == ORDER_STATUS_COMPLETED:
        return "已完成取件"
    if order.status == ORDER_STATUS_READY_FOR_PICKUP:
        return "已入柜，等待取件"
    if order.status in (ORDER_STATUS_DISPATCHED, ORDER_STATUS_WAITING_AI):
        return "配送中"
    if order.status in (ORDER_STATUS_EXCEPTION, ORDER_STATUS_CANCELLED) or (task and (task.exception_status or "none") == "pending"):
        return "异常处理中"
    return "订单已创建"


def _build_qr_verify_view(order: PlatformOrder):
    task = get_latest_task_for_order(order.platform_order_id)
    pkg = Package.query.get(task.package_id) if task and task.package_id else None
    box_no = (pkg.box_no if pkg and pkg.box_no else "") or (task.box_no if task and task.box_no else "")

    return {
        "receiverName": order.receiver_name or "-",
        "merchantName": order.merchant_name or "-",
        "boxNo": box_no or "-",
        "statusText": _qr_status_text(order, task, pkg),
        "verifyCode": order.verify_code or "-",
    }


@platform_bp.get("/api/order/verify_qr/<string:token>")
def verify_order_qr(token):
    cleaned = (token or "").strip()
    if not cleaned:
        return jsonify({"status": "error", "message": "二维码无效或订单不存在"}), 404

    order = PlatformOrder.query.filter_by(qr_token=cleaned).first()
    if not order:
        return jsonify({"status": "error", "message": "二维码无效或订单不存在"}), 404

    return jsonify({"status": "success", **_build_qr_verify_view(order)})


@platform_bp.post("/api/platform/mock/push_order")
def mock_push_order():
    payload = request.get_json(silent=True) or {}

    try:
        order, created, normalized = upsert_platform_order_from_payload(
            payload,
            source="mock",
            provider_name="mock-platform",
            actor_type="platform",
            actor_openid="mock-platform",
        )
    except PlatformPayloadError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
            "reasonCode": exc.reason_code,
        }), exc.http_code
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    db.session.commit()
    emit_platform_event("order_received", order=order)

    return jsonify({
        "status": "success",
        "created": created,
        "normalized": normalized,
        "order": _build_order_view(order),
    })


@platform_bp.get("/api/platform/orders")
def list_platform_orders():
    status = (request.args.get("status") or "").strip()
    platform_name = (request.args.get("platformName") or request.args.get("platform_name") or "").strip()
    external_order_no = (request.args.get("externalOrderNo") or request.args.get("external_order_no") or "").strip()

    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        limit = 50
    limit = min(max(limit, 1), 200)

    query = PlatformOrder.query
    if status:
        query = query.filter_by(status=status)
    if platform_name:
        query = query.filter_by(platform_name=platform_name)
    if external_order_no:
        query = query.filter_by(external_order_no=external_order_no)

    orders = query.order_by(PlatformOrder.created_at.desc()).limit(limit).all()

    return jsonify({
        "status": "success",
        "items": [_build_order_view(order) for order in orders],
        "count": len(orders),
    })


@platform_bp.get("/api/platform/orders/<string:platform_order_id>")
def get_platform_order(platform_order_id):
    order = PlatformOrder.query.get(platform_order_id)
    if not order:
        return jsonify({"status": "error", "message": "Platform order not found"}), 404

    return jsonify({"status": "success", "order": _build_order_view(order)})


@platform_bp.post("/api/platform/orders/<string:platform_order_id>/dispatch")
@platform_bp.post("/api/admin/platform/orders/<string:platform_order_id>/dispatch")
def dispatch_platform_order(platform_order_id):
    admin_actor_openid = None
    if request.path.startswith("/api/admin/"):
        admin_user = authenticate_admin_request(persist_session=False)
        if not admin_user:
            token = get_request_token()
            if not token:
                return jsonify({"status": "error", "message": "Missing admin token"}), 401

            candidate = resolve_admin_by_token(token)
            if not candidate:
                return jsonify({"status": "error", "message": "Invalid token"}), 401

            return jsonify({"status": "error", "message": "Admin permission required"}), 403

        admin_actor_openid = admin_user.openid

    order = PlatformOrder.query.get(platform_order_id)
    if not order:
        return jsonify({"status": "error", "message": "Platform order not found"}), 404

    if order.status in (ORDER_STATUS_READY_FOR_PICKUP, ORDER_STATUS_COMPLETED):
        return jsonify({
            "status": "error",
            "message": f"Order cannot be dispatched in current status: {order.status}",
        }), 409

    data = request.get_json(silent=True) or {}
    courier_openid = (data.get("courierOpenid") or data.get("courier_openid") or "").strip()

    try:
        task, created = dispatch_order_to_task(order, courier_openid)
    except ValueError as exc:
        message = str(exc)
        code = 400 if "Missing courierOpenid" in message else 409
        return jsonify({"status": "error", "message": message}), code
    except RuntimeError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409

    db.session.commit()
    if created:
        log_action(
            "platform_order.dispatched",
            actor_type="admin" if admin_actor_openid else "platform",
            actor_openid=admin_actor_openid or "platform_api",
            platform_order_id=order.platform_order_id,
            task_id=task.task_id,
            detail=f"平台接口派单给配送员：{courier_openid}",
        )
        db.session.commit()
        emit_platform_event("order_dispatched", order=order, task=task)
        emit_hardware_event("task_created", order=order, task=task)

    return jsonify({
        "status": "success",
        "created": created,
        "order": _build_order_view(order),
        "task": task.to_dict(),
    })
