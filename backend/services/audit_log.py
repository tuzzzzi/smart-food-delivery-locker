from models import AdminLog


ACTION_LABELS = {
    "platform_order.created": "平台订单创建",
    "platform_order.dispatched": "管理员派单",
    "platform_order.cancelled": "订单取消",
    "platform_order.exception_closed": "异常处理收口",
    "task.put_in_confirmed": "配送员确认入柜",
    "task.cancelled_by_courier": "配送员取消任务",
    "task.submitted_to_admin": "配送员上报管理员处理",
    "task.reset_for_retry": "配送员重置任务",
    "task.ai_failed": "AI 检测未通过",
    "task.ai_passed": "AI 检测通过",
    "task.ai_runtime_error": "AI 运行异常",
    "task.reset_by_admin": "管理员重置任务",
    "task.rolled_back_by_admin": "管理员回退任务",
    "task.admin_repair_started": "管理员开始维修处理",
    "task.admin_contact_buyer": "管理员待联系买家",
    "task.admin_exception_resolved": "管理员标记异常已处理完成",
    "task.admin_exception_note_updated": "管理员更新异常说明",
    "package.ready_for_pickup": "包裹待取件",
    "package.picked_up": "用户完成取件",
    "locker.command_created": "柜门命令已创建",
    "locker.command_ack": "柜门命令已下发",
    "locker.lock_feedback": "锁反馈已回传",
    "locker.door_sensor": "门磁状态已回传",
    "locker.command_timeout": "柜门开门超时",
    "locker.door_close_timeout": "柜门未按时关闭",
    "locker.command_failed": "柜门执行失败",
    "locker.sequence_error": "柜门回调顺序异常",
    "locker.business_completed": "硬件回调完成业务收口",
    "locker.business_exception": "硬件回调进入异常收口",
}

ACTOR_LABELS = {
    "admin": "管理员",
    "courier": "配送员",
    "user": "用户",
    "system": "系统",
    "platform": "平台",
    "hardware": "硬件",
}


def log_action(
    action: str,
    *,
    actor_type: str,
    actor_openid: str = None,
    platform_order_id: str = None,
    task_id: str = None,
    package_id: str = None,
    admin_id: int = None,
    detail: str = None,
):
    log = AdminLog(
        action=action,
        platform_order_id=platform_order_id,
        task_id=task_id,
        package_id=package_id,
        admin_id=admin_id,
        actor_type=actor_type,
        actor_openid=actor_openid,
        detail=(detail or "").strip()[:255] if detail else None,
    )
    from extensions import db

    db.session.add(log)
    return log


def _actor_label(log: AdminLog):
    base = ACTOR_LABELS.get((log.actor_type or "").strip(), log.actor_type or "操作者")
    actor_openid = (log.actor_openid or "").strip()
    if actor_openid:
        return f"{base} / {actor_openid}"
    return base


def serialize_log(log: AdminLog):
    data = log.to_dict()
    data["actionLabel"] = ACTION_LABELS.get(log.action, log.action)
    data["actorLabel"] = _actor_label(log)
    data["createdAtDt"] = log.created_at
    return data
