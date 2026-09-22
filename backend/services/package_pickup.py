import time

from extensions import db
from models import Box, Package
from services.audit_log import log_action
from services.platform_flow import (
    emit_hardware_event,
    emit_platform_event,
    mark_order_completed_for_package,
)
from utils.remind_utils import on_package_picked, stop_package_future_reminds


def _finish_pick(pkg: Package):
    box = Box.query.get(pkg.box_no)
    if box:
        box.status = "empty"
        box.task_id = None

    now_ts = int(time.time())
    pkg.status = "picked"
    pkg.picked_at = now_ts

    if pkg.receiver_openid and pkg.arrived_at:
        on_package_picked(pkg.receiver_openid, pkg.package_id, pkg.arrived_at, pkg.picked_at)
    else:
        stop_package_future_reminds(pkg.package_id)

    return mark_order_completed_for_package(pkg)


def finalize_package_pickup(pkg: Package, *, actor_type: str, actor_openid: str, detail: str):
    if not pkg:
        raise ValueError("Package not found")

    if pkg.status == "picked":
        order = mark_order_completed_for_package(pkg)
        return order, False

    order = _finish_pick(pkg)
    log_action(
        "package.picked_up",
        actor_type=actor_type,
        actor_openid=actor_openid,
        platform_order_id=getattr(order, "platform_order_id", None),
        task_id=pkg.task_id,
        package_id=pkg.package_id,
        detail=detail,
    )
    emit_platform_event("order_completed", order=order, pkg=pkg)
    emit_hardware_event("package_picked", order=order, pkg=pkg)
    return order, True
