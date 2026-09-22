from flask import Blueprint, g, jsonify, request
from datetime import datetime, timedelta
from sqlalchemy import or_
from extensions import db
from routes.guards import admin_api_required, user_api_required
from models import PickupHistory, RemindEvent, Package  # ✅加 Package 用于 packageId -> openid

remind_bp = Blueprint("remind", __name__)


def _current_user_openid() -> str:
    return (getattr(getattr(g, "current_user", None), "openid", "") or "").strip()

# ---------- 规则：用最近5次取件耗时的“中位数”决定提醒次数 ----------
def _median(nums):
    nums = sorted(nums)
    n = len(nums)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return nums[mid]
    return int((nums[mid - 1] + nums[mid]) / 2)

def _bucket(sec):
    if sec is None:
        return "A"  # 新用户默认只提醒一次
    if sec <= 30 * 60:
        return "A"
    if sec <= 60 * 60:
        return "B"
    if sec <= 90 * 60:
        return "C"
    if sec <= 120 * 60:
        return "D"
    return "E"

def _offsets_minutes(bucket):
    if bucket == "A": return [0]
    if bucket == "B": return [0, 30]
    if bucket == "C": return [0, 30, 60]
    if bucket == "D": return [0, 30, 60, 90]
    return [0, 30, 60, 90, 120]  # E

def _user_bucket(openid: str) -> str:
    rows = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .limit(5).all())
    secs = [r.duration_sec for r in rows if r.duration_sec is not None]
    return _bucket(_median(secs))

def _bucket_stats(secs):
    """
    secs: list[int] 取件耗时秒
    返回各区间数量，用于前端展示/论文加分
    """
    stats = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for s in secs:
        stats[_bucket(s)] += 1
    return stats

# ---------- 自动提醒：入柜时入队（你现在可先不用，但保留） ----------
def enqueue_auto_reminds(openid: str, package_id: str, arrived_at_ts: int):
    """
    ✅ 在“包裹入柜(写 arrived_at)”时调用：
    根据用户最近5次取件习惯，生成 1~5 条提醒事件
    """
    if not openid or not package_id or not arrived_at_ts:
        return

    bucket = _user_bucket(openid)
    offsets = _offsets_minutes(bucket)
    base = datetime.utcfromtimestamp(int(arrived_at_ts))

    for m in offsets:
        msg = "外卖已入柜，请及时取餐"
        if m >= 60:
            msg = "外卖已入柜较久，请尽快取餐避免影响口感"

        ev = RemindEvent(
            openid=openid,
            package_id=package_id,
            due_at=base + timedelta(minutes=m),
            message=msg,
            source="auto"
        )
        db.session.add(ev)
    # ✅ 注意：这里不 commit，交给外层事务统一 commit

def stop_package_reminds(package_id: str):
    """
    ✅ 取件完成后停止该包裹所有未“送达”的提醒（直接标记 sent_at）
    这样 poll 不会再弹
    """
    if not package_id:
        return
    now = datetime.utcnow()
    (RemindEvent.query
        .filter(RemindEvent.package_id == package_id)
        .filter(RemindEvent.sent_at.is_(None))
        .update({"sent_at": now}, synchronize_session=False))
    # 注意：需要外层 commit

def record_pickup_history(openid: str, package_id: str, duration_sec: int):
    """
    ✅ 写入历史并只保留最近5次
    """
    if not openid or duration_sec is None:
        return

    db.session.add(PickupHistory(
        openid=openid,
        package_id=package_id,
        duration_sec=int(duration_sec)
    ))

    db.session.flush()

    rows = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .all())
    if len(rows) > 5:
        for r in rows[5:]:
            db.session.delete(r)

# ---------- 用户端轮询：到点提醒（会写 sent_at，避免重复弹） ----------
@remind_bp.get("/api/remind/poll")
@user_api_required
def poll_remind():
    openid = _current_user_openid()

    now = datetime.utcnow()

    evs = (RemindEvent.query
           .filter_by(openid=openid)
           .filter(RemindEvent.sent_at.is_(None))
           .filter(RemindEvent.due_at <= now)
           .order_by(RemindEvent.due_at.asc())
           .limit(3).all())

    reminds = []
    for e in evs:
        reminds.append({
            "id": e.id,
            "packageId": e.package_id,
            "message": e.message,
            "source": e.source,
        })
        e.sent_at = now  # ✅代表“已送达前端/已弹出”

    db.session.commit()
    return jsonify({"status": "success", "reminds": reminds})


# ---------- 通知列表：remind-list 页面就用这个 ----------
@remind_bp.get("/api/remind/list")
@user_api_required
def remind_list():
    """
    通知中心列表：返回该用户的提醒（未来/已送达/未读/已读都在这里）
    GET /api/remind/list?openid=xxx&limit=50
    """
    openid = _current_user_openid()

    limit = int(request.args.get("limit") or 50)
    limit = min(max(limit, 1), 200)

    evs = (RemindEvent.query
           .filter_by(openid=openid)
           .order_by(RemindEvent.created_at.desc())
           .limit(limit)
           .all())

    items = []
    for e in evs:
        items.append({
            "id": e.id,
            "packageId": e.package_id,
            "message": e.message,
            "source": e.source,
            "dueAt": e.due_at.isoformat() if e.due_at else None,
            "sentAt": e.sent_at.isoformat() if e.sent_at else None,
            "readAt": e.read_at.isoformat() if e.read_at else None,
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        })

    return jsonify({"status": "success", "items": items})


# ---------- 标记已读：remind-list 页面点“全部已读/已读某条” ----------
@remind_bp.post("/api/remind/mark_read")
@user_api_required
def mark_read():
    """
    POST { openid, ids:[1,2,3] } 或 { openid, all:true }
    """
    data = request.get_json(silent=True) or {}
    openid = _current_user_openid()

    now = datetime.utcnow()

    if data.get("all"):
        (RemindEvent.query
            .filter_by(openid=openid)
            .filter(RemindEvent.read_at.is_(None))
            .update({"read_at": now}, synchronize_session=False))
        db.session.commit()
        return jsonify({"status": "success", "message": "ok"})

    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"status": "error", "message": "Missing ids"}), 400

    (RemindEvent.query
        .filter_by(openid=openid)
        .filter(RemindEvent.id.in_(ids))
        .update({"read_at": now}, synchronize_session=False))

    db.session.commit()
    return jsonify({"status": "success", "message": "ok"})


# ---------- 未读数量：给 user-home 的红点 ----------
@remind_bp.get("/api/remind/unread_count")
@user_api_required
def unread_count():
    """
    未读 = read_at为空 且（sent_at非空 或 due_at<=now）
    ✅管理员手动提醒 due_at=now，即使用户没 poll，也会立刻显示红点
    """
    openid = _current_user_openid()

    now = datetime.utcnow()

    cnt = (RemindEvent.query
           .filter_by(openid=openid)
           .filter(RemindEvent.read_at.is_(None))
           .filter(or_(
               RemindEvent.sent_at.isnot(None),
               RemindEvent.due_at <= now
           ))
           .count())

    return jsonify({"status": "success", "count": cnt})


# ---------- 管理员手动提醒：立刻入队（兼容 packageId 自动找 openid） ----------
@remind_bp.post("/api/admin/remind/manual")
@admin_api_required
def admin_manual_remind():
    data = request.get_json(silent=True) or {}
    openid = (data.get("openid") or "").strip()
    package_id = (data.get("packageId") or "").strip() or None
    message = (data.get("message") or "").strip() or "管理员提醒：请及时取餐"

    # ✅允许只给 packageId，由后端找 receiver_openid
    if not openid and package_id:
        pkg = Package.query.get(package_id)
        if pkg and pkg.receiver_openid:
            openid = pkg.receiver_openid

    if not openid:
        return jsonify({"status": "error", "message": "Missing openid"}), 400

    ev = RemindEvent(
        openid=openid,
        package_id=package_id,
        due_at=datetime.utcnow(),
        message=message[:255],
        source="admin"
    )
    db.session.add(ev)
    db.session.commit()

    return jsonify({"status": "success", "message": "已发送提醒（进入提醒队列）"})


# ---------- 取件统计：获取用户最近5次取件信息（用于展示/论文/调参） ----------
@remind_bp.get("/api/remind/user_pickup_stats")
@user_api_required
def user_pickup_stats():
    """
    GET /api/remind/user_pickup_stats?openid=xxx
    返回：最近5次耗时、median、bucket、区间统计
    """
    openid = _current_user_openid()

    rows = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .limit(5).all())

    secs = [r.duration_sec for r in rows if r.duration_sec is not None]
    med = _median(secs) if secs else None
    bucket = _bucket(med)
    stats = _bucket_stats(secs)

    return jsonify({
        "status": "success",
        "bucket": bucket,
        "medianSec": med,
        "recent": [
            {
                "durationSec": r.duration_sec,
                "packageId": r.package_id,
                "createdAt": r.created_at.isoformat() if r.created_at else None
            } for r in rows
        ],
        "bucketStats": stats
    })
