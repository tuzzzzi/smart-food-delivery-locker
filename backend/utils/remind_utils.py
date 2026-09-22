# utils/remind_utils.py
from datetime import datetime, timedelta
import json

from extensions import db
from models import PickupHistory, RemindEvent, RemindPolicy


# =========================
#  A) 统计：最近5次耗时 -> 中位数 -> A~E 档
# =========================

def _median(nums):
    nums = sorted(nums)
    n = len(nums)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return nums[mid]
    return int((nums[mid - 1] + nums[mid]) / 2)


def _bucket_from_sec(sec):
    """
    A: <=30min
    B: 30-60
    C: 60-90
    D: 90-120
    E: >=120
    """
    if sec is None:
        return "A"  # 新用户默认 A：只提醒一次
    if sec <= 30 * 60:
        return "A"
    if sec <= 60 * 60:
        return "B"
    if sec <= 90 * 60:
        return "C"
    if sec <= 120 * 60:
        return "D"
    return "E"


def _offsets_minutes_from_bucket(bucket: str):
    """
    入柜时刻 T0 的偏移分钟数列表：决定提醒次数
    """
    if bucket == "A":
        return [0]
    if bucket == "B":
        return [0, 30]
    if bucket == "C":
        return [0, 30, 60]
    if bucket == "D":
        return [0, 30, 60, 90]
    return [0, 30, 60, 90, 120]  # E


def get_user_pickup_bucket(openid: str) -> str:
    """
    读取用户最近 5 次取件耗时，取中位数落档 A~E
    """
    if not openid:
        return "A"
    rows = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .limit(5).all())
    secs = [int(r.duration_sec) for r in rows if r.duration_sec is not None]
    return _bucket_from_sec(_median(secs))


# =========================
#  A2) 策略：RemindPolicy -> offsets
# =========================

def _parse_offsets_json(s: str):
    """
    offsets_json: "[0,30,60]" -> [0,30,60]
    """
    if not s:
        return []
    try:
        arr = json.loads(s)
        if not isinstance(arr, list):
            return []
        out = []
        for x in arr:
            if isinstance(x, int) and x >= 0:
                out.append(x)
        out = sorted(set(out))
        return out[:10]
    except Exception:
        return []


def _get_policy(openid: str):
    """
    没有策略则返回 None（外层回退 auto）
    """
    if not openid:
        return None
    return RemindPolicy.query.filter_by(openid=openid).first()


def _offsets_from_policy(openid: str):
    """
    返回: (policy_mode_tag, offsets_minutes)
    - policy_mode_tag 用于写入 source，便于你后台展示 auto:fixed / auto:custom
    """
    policy = _get_policy(openid)
    if not policy or not policy.enabled:
        bucket = get_user_pickup_bucket(openid)
        return ("auto", _offsets_minutes_from_bucket(bucket))

    mode = (policy.mode or "auto").strip()

    # custom：直接用 offsets_json
    if mode == "custom":
        offsets = _parse_offsets_json(policy.offsets_json or "")
        if offsets:
            return ("custom", offsets)
        # 配坏了 -> 回退 auto
        bucket = get_user_pickup_bucket(openid)
        return ("auto_fallback", _offsets_minutes_from_bucket(bucket))

    # fixed：固定次数 N，默认间隔 30min
    if mode == "fixed":
        try:
            n = int(policy.fixed_count or 0)
        except Exception:
            n = 0
        if n < 1:
            n = 1
        if n > 5:
            n = 5
        offsets = [i * 30 for i in range(n)]  # 0,30,60...
        return ("fixed", offsets)

    # auto：按桶
    bucket = get_user_pickup_bucket(openid)
    return ("auto", _offsets_minutes_from_bucket(bucket))


# =========================
#  B) 自动提醒：入柜时生成 1~5 条 RemindEvent
# =========================

def enqueue_auto_reminds(openid: str, package_id: str, arrived_at_ts: int):
    """
    ✅ 在 AI通过入柜（写 arrived_at）后调用：
    根据 RemindPolicy（auto/fixed/custom）生成 1~5 条提醒事件（站内通知）

    注意：
    - 这里只 db.session.add，不 commit（由外层统一 commit）
    - 幂等：同 package_id 已生成过提醒就不再生成（避免刷新/重试翻倍）
    """
    if not openid or not package_id or not arrived_at_ts:
        return

    # ✅ 幂等：已有提醒则跳过
    exists = (RemindEvent.query
              .filter(RemindEvent.openid == openid, RemindEvent.package_id == package_id)
              .count())
    if exists > 0:
        return

    mode_tag, offsets = _offsets_from_policy(openid)
    base = datetime.utcfromtimestamp(int(arrived_at_ts))

    for m in offsets:
        msg = "外卖已入柜，请及时取餐"
        if int(m) >= 60:
            msg = "外卖已入柜较久，请尽快取餐避免影响口感"

        ev = RemindEvent(
            openid=openid,
            package_id=package_id,
            due_at=base + timedelta(minutes=int(m)),
            message=msg,
            source=f"auto:{mode_tag}",
            sent_at=None,
            read_at=None
        )
        db.session.add(ev)


def enqueue_admin_remind(openid: str, package_id: str = None, message: str = None):
    """
    ✅ 管理员手动提醒（可在 admin 路由里用函数而不是直接写表）
    注意：这里只 add，不 commit
    """
    if not openid:
        return
    msg = (message or "管理员提醒：请及时取餐")[:255]
    ev = RemindEvent(
        openid=openid,
        package_id=package_id,
        due_at=datetime.utcnow(),
        message=msg,
        source="admin",
        sent_at=None,
        read_at=None
    )
    db.session.add(ev)


# =========================
#  C) 取件完成：写历史(最近5次) + 停止提醒
# =========================

def record_pickup_history_and_prune(openid: str, package_id: str, arrived_at_ts: int, picked_at_ts: int):
    """
    ✅ 写一条历史耗时，并裁剪只保留 5 条
    arrived_at_ts / picked_at_ts 都是 int timestamp(秒)
    注意：这里只 add/flush/delete，不 commit
    """
    if not openid or not arrived_at_ts or not picked_at_ts:
        return

    duration = int(picked_at_ts) - int(arrived_at_ts)
    if duration < 0:
        duration = 0

    db.session.add(PickupHistory(
        openid=openid,
        package_id=package_id,
        duration_sec=int(duration)
    ))

    db.session.flush()

    rows = (PickupHistory.query
            .filter_by(openid=openid)
            .order_by(PickupHistory.created_at.desc())
            .all())
    if len(rows) > 5:
        for r in rows[5:]:
            db.session.delete(r)


def stop_package_future_reminds(package_id: str):
    """
    ✅ 取件完成后：停止该包裹所有未送达提醒
    做法：把 sent_at 置为 now（代表这些提醒不再弹）

    更稳：不光停 sent_at=None 的，还会把 due_at 在未来的也停掉（因为它们还没到点）
    """
    if not package_id:
        return
    now = datetime.utcnow()

    (RemindEvent.query
        .filter(RemindEvent.package_id == package_id)
        .filter(RemindEvent.sent_at.is_(None))
        .update({"sent_at": now}, synchronize_session=False))

    # 可选：如果你未来允许“已送达但未读”的提醒继续存在，就不要动 read_at
    # 这里不改 read_at


def clear_package_all_reminds(package_id: str):
    """
    （可选）删除某包裹全部提醒事件：用于调试/重置
    """
    if not package_id:
        return
    (RemindEvent.query
        .filter(RemindEvent.package_id == package_id)
        .delete(synchronize_session=False))


# =========================
#  D) 最省事组合：取件完成时调用
# =========================

def on_package_picked(openid: str, package_id: str, arrived_at_ts: int, picked_at_ts: int):
    """
    ✅ 用户取件完成时调用：
    - 写取件历史（并保留最近5次）
    - 停止该包裹后续提醒
    注意：不 commit，由外层统一 commit
    """
    record_pickup_history_and_prune(openid, package_id, arrived_at_ts, picked_at_ts)
    stop_package_future_reminds(package_id)