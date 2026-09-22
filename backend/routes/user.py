from flask import Blueprint, g, jsonify, request

from extensions import db
from models import Package, PlatformOrder, Task
from routes.guards import user_api_required
from services.locker_gateway import (
    BUSINESS_COMPLETED,
    get_box_locker_status,
    get_latest_package_locker_command,
    mark_package_locker_business_state,
    request_open_door,
    serialize_locker_trace,
)
from services.ownership import (
    backfill_package_receiver_chain,
    resolve_package_receiver_openid,
)
from services.package_pickup import finalize_package_pickup

user_bp = Blueprint("user_bp", __name__)


def ok(data=None, code=200):
    payload = {"status": "success"}
    if data:
        payload.update(data)
    return jsonify(payload), code


def err(message, code=200, **extra):
    payload = {"status": "error", "message": message}
    payload.update(extra)
    return jsonify(payload), code


def _current_user():
    return getattr(g, "current_user", None)


def _current_user_openid() -> str:
    return (getattr(_current_user(), "openid", "") or "").strip()


def _package_hardware_snapshot(pkg: Package):
    if not pkg or not pkg.box_no:
        return None

    hardware = get_box_locker_status(pkg.box_no)
    latest = (hardware or {}).get("latestCommand") or {}
    if latest and latest.get("packageId") and latest.get("packageId") != pkg.package_id:
        return None
    return hardware


def _package_deposit_evidence(pkg: Package):
    if not pkg:
        return None

    command = get_latest_package_locker_command(pkg.package_id, scene="deposit")
    return serialize_locker_trace(command, audience="user") if command else None


def _package_verify_token(pkg: Package):
    if not pkg or not pkg.task_id:
        return ""
    task = Task.query.get(pkg.task_id)
    if not task or not task.platform_order_id:
        return ""
    order = PlatformOrder.query.get(task.platform_order_id)
    return order.qr_token if order and order.qr_token else ""


def _serialize_user_package(pkg: Package):
    item = pkg.to_dict()
    item["verifyToken"] = _package_verify_token(pkg)
    return item


def _request_pickup_open_door(pkg: Package, operator_openid: str, reason: str):
    return request_open_door(
        pkg.box_no,
        operator_type="user",
        operator_id=operator_openid,
        reason=reason,
        task_id=pkg.task_id,
        package_id=pkg.package_id,
    )


def _ensure_package_owner(pkg: Package) -> bool:
    if not pkg:
        return False

    current_openid = _current_user_openid()
    if not current_openid:
        return False

    owner_openid = resolve_package_receiver_openid(
        pkg,
        allow_phone_lookup=False,
        allow_demo_fallback=False,
    )
    if owner_openid:
        backfill_package_receiver_chain(pkg, owner_openid)
        return owner_openid == current_openid

    return False


def _query_current_user_packages(status: str):
    current_openid = _current_user_openid()

    packages = (
        Package.query
        .filter(Package.status == status)
        .filter(Package.receiver_openid == current_openid)
        .order_by(Package.updated_at.desc(), Package.created_at.desc())
        .all()
    )

    changed = False
    result = []
    for pkg in packages:
        before = (pkg.receiver_openid or "").strip()
        if _ensure_package_owner(pkg):
            result.append(pkg)
            if before != (pkg.receiver_openid or "").strip():
                changed = True

    if changed:
        db.session.commit()

    return result


def _pending_hardware_response(door_result, pkg: Package):
    return ok({
        "message": "Open-door command created. Waiting for ESP32 polling and hardware callbacks.",
        "pendingHardware": True,
        "package": _serialize_user_package(pkg),
        "hardware": door_result,
        "depositEvidence": _package_deposit_evidence(pkg),
        "integration": {"locker": door_result},
    }, 202)


def _finalize_pick(pkg: Package, actor_openid: str, detail: str):
    order, _ = finalize_package_pickup(
        pkg,
        actor_type="user",
        actor_openid=actor_openid,
        detail=detail,
    )
    mark_package_locker_business_state(
        package_id=pkg.package_id,
        status=BUSINESS_COMPLETED,
        note="Mock pickup flow completed by user request.",
        actor_type="user",
        actor_openid=actor_openid,
    )
    db.session.commit()
    return order


@user_bp.get("/api/user/home")
@user_api_required
def user_home():
    pending = sorted(
        _query_current_user_packages("pending"),
        key=lambda pkg: pkg.arrived_at or 0,
        reverse=True,
    )
    history = _query_current_user_packages("picked")[:5]

    return ok({
        "pending": [_serialize_user_package(pkg) for pkg in pending],
        "history": [_serialize_user_package(pkg) for pkg in history],
    })


@user_bp.post("/api/user/find_by_code")
@user_api_required
def user_find_by_code():
    data = request.get_json(silent=True) or {}
    pickup_code = (data.get("pickupCode") or "").strip()

    if not pickup_code:
        return err("Missing pickupCode", 400)
    if not (len(pickup_code) == 6 and pickup_code.isdigit()):
        return err("pickupCode must be 6 digits", 400)

    packages = (
        Package.query
        .filter(Package.pickup_code == pickup_code)
        .order_by(Package.created_at.desc())
        .all()
    )

    pkg = None
    changed = False
    for candidate in packages:
        before = (candidate.receiver_openid or "").strip()
        if _ensure_package_owner(candidate):
            pkg = candidate
            if before != (candidate.receiver_openid or "").strip():
                changed = True
            break

    if changed:
        db.session.commit()

    if not pkg:
        return err("Package not found for current user", 200)

    return ok({
        "package": _serialize_user_package(pkg),
        "depositEvidence": _package_deposit_evidence(pkg),
    })


@user_bp.get("/api/user/package")
@user_api_required
def user_get_package():
    package_id = (request.args.get("packageId") or "").strip()
    if not package_id:
        return err("Missing packageId", 400)

    pkg = Package.query.get(package_id)
    if not pkg:
        return err("Package not found", 404)
    if not _ensure_package_owner(pkg):
        return err("Forbidden package access", 403)

    db.session.commit()
    return ok({
        "package": _serialize_user_package(pkg),
        "hardware": _package_hardware_snapshot(pkg),
        "depositEvidence": _package_deposit_evidence(pkg),
    })


@user_bp.post("/api/user/open_door")
@user_api_required
def user_open_door():
    data = request.get_json(silent=True) or {}
    package_id = (data.get("packageId") or "").strip()
    pickup_code = (data.get("pickupCode") or "").strip()

    if not package_id or not pickup_code:
        return err("Missing packageId or pickupCode", 400)

    pkg = Package.query.get(package_id)
    if not pkg:
        return err("Package not found", 404)
    if not _ensure_package_owner(pkg):
        return err("Forbidden package access", 403)
    if pkg.status != "pending":
        message = "Package already picked" if pkg.status == "picked" else "Package is not pending"
        return err(
            message,
            409,
            reasonCode="package_already_picked" if pkg.status == "picked" else "package_not_pending",
            package=_serialize_user_package(pkg),
            hardware=_package_hardware_snapshot(pkg),
            depositEvidence=_package_deposit_evidence(pkg),
        )
    if pkg.pickup_code != pickup_code:
        return err("Invalid pickup code", 401)

    current_openid = _current_user_openid()
    db.session.commit()
    door_result = _request_pickup_open_door(pkg, current_openid, "pickup_code")
    if door_result.get("conflict"):
        db.session.commit()
        if door_result.get("reusedExistingCommand") and door_result.get("pendingHardware"):
            return _pending_hardware_response(door_result, pkg)
        return err(
            door_result.get("note") or "Another locker command is still in progress",
            409,
            package=_serialize_user_package(pkg),
            hardware=get_box_locker_status(pkg.box_no),
            depositEvidence=_package_deposit_evidence(pkg),
            integration={"locker": door_result},
        )
    if door_result.get("pendingHardware"):
        db.session.commit()
        return _pending_hardware_response(door_result, pkg)
    if not door_result.get("accepted"):
        db.session.commit()
        return err("Locker controller unavailable", 503, integration=door_result)

    _finalize_pick(pkg, current_openid, "User pickup completed by pickup code")
    return ok({
        "message": "Door opened and pickup completed.",
        "package": _serialize_user_package(pkg),
        "hardware": _package_hardware_snapshot(pkg),
        "depositEvidence": _package_deposit_evidence(pkg),
        "integration": {"locker": door_result},
    })


@user_bp.post("/api/user/open_door_by_package")
@user_api_required
def open_door_by_package():
    data = request.get_json(silent=True) or {}
    package_id = (data.get("packageId") or "").strip()

    if not package_id:
        return err("Missing packageId", 400)

    pkg = Package.query.get(package_id)
    if not pkg:
        return err("Package not found", 404)
    if not _ensure_package_owner(pkg):
        return err("Forbidden package access", 403)
    if pkg.status != "pending":
        message = "Package already picked" if pkg.status == "picked" else "Package is not pending"
        return err(
            message,
            409,
            reasonCode="package_already_picked" if pkg.status == "picked" else "package_not_pending",
            package=_serialize_user_package(pkg),
            hardware=_package_hardware_snapshot(pkg),
            depositEvidence=_package_deposit_evidence(pkg),
        )

    current_openid = _current_user_openid()
    db.session.commit()
    door_result = _request_pickup_open_door(pkg, current_openid, "package_detail")
    if door_result.get("conflict"):
        db.session.commit()
        if door_result.get("reusedExistingCommand") and door_result.get("pendingHardware"):
            return _pending_hardware_response(door_result, pkg)
        return err(
            door_result.get("note") or "Another locker command is still in progress",
            409,
            package=_serialize_user_package(pkg),
            hardware=get_box_locker_status(pkg.box_no),
            depositEvidence=_package_deposit_evidence(pkg),
            integration={"locker": door_result},
        )
    if door_result.get("pendingHardware"):
        db.session.commit()
        return _pending_hardware_response(door_result, pkg)
    if not door_result.get("accepted"):
        db.session.commit()
        return err("Locker controller unavailable", 503, integration=door_result)

    _finalize_pick(pkg, current_openid, "User pickup completed by package detail")
    return ok({
        "message": "Door opened and pickup completed.",
        "package": _serialize_user_package(pkg),
        "hardware": _package_hardware_snapshot(pkg),
        "depositEvidence": _package_deposit_evidence(pkg),
        "integration": {"locker": door_result},
    })
