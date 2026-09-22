import json
from datetime import datetime

from sqlalchemy import UniqueConstraint

from extensions import db


class PlatformOrder(db.Model):
    __tablename__ = "platform_orders"
    __table_args__ = (
        UniqueConstraint("platform_name", "external_order_no", name="uq_platform_orders_platform_external"),
        UniqueConstraint("qr_token", name="uq_platform_orders_qr_token"),
    )

    platform_order_id = db.Column(db.String(64), primary_key=True)  # "PO-xxx"
    platform_name = db.Column(db.String(50), nullable=False, default="mock", index=True)
    external_order_no = db.Column(db.String(64), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, default="mock")
    status = db.Column(db.String(30), nullable=False, default="pending_dispatch", index=True)

    receiver_name = db.Column(db.String(100), nullable=True)
    receiver_phone = db.Column(db.String(20), nullable=False, index=True)
    receiver_address = db.Column(db.String(255), nullable=True)
    receiver_openid = db.Column(db.String(64), nullable=True, index=True)

    merchant_name = db.Column(db.String(100), nullable=True)
    merchant_note = db.Column(db.String(255), nullable=True)

    courier_openid = db.Column(db.String(64), nullable=True, index=True)
    raw_payload = db.Column(db.Text, nullable=True)
    qr_token = db.Column(db.String(64), nullable=True, index=True)
    qr_image_path = db.Column(db.String(255), nullable=True)
    verify_code = db.Column(db.String(12), nullable=True)

    pushed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    dispatched_at = db.Column(db.DateTime, nullable=True, index=True)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)
    closed_at = db.Column(db.DateTime, nullable=True, index=True)
    status_note = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def to_dict(self):
        raw_payload = self.raw_payload
        if raw_payload:
            try:
                raw_payload = json.loads(raw_payload)
            except Exception:
                pass

        return {
            "platformOrderId": self.platform_order_id,
            "platformName": self.platform_name,
            "externalOrderNo": self.external_order_no,
            "source": self.source,
            "status": self.status,
            "receiverName": self.receiver_name,
            "receiverPhone": self.receiver_phone,
            "receiverAddress": self.receiver_address,
            "receiverOpenid": self.receiver_openid,
            "merchantName": self.merchant_name,
            "merchantNote": self.merchant_note,
            "courierOpenid": self.courier_openid,
            "rawPayload": raw_payload,
            "pushedAt": self.pushed_at.isoformat() if self.pushed_at else None,
            "dispatchedAt": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "closedAt": self.closed_at.isoformat() if self.closed_at else None,
            "statusNote": self.status_note,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class User(db.Model):
    __tablename__ = "users"
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    openid = db.Column(db.String(64), unique=True, nullable=True)  # 微信openid（mock也可以）
    username = db.Column(db.String(100), nullable=True)

    role = db.Column(db.String(50), nullable=False, default="user")  # user/courier/admin
    phone_number = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(100), nullable=True)
    receiver_name = db.Column(db.String(100), nullable=True)
    pickup_area = db.Column(db.String(100), nullable=True)
    backup_phone = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "userId": self.user_id,
            "openid": self.openid,
            "username": self.username,
            "role": self.role,
            "phoneNumber": self.phone_number,
            "email": self.email,
            "receiverName": self.receiver_name,
            "pickupArea": self.pickup_area,
            "backupPhone": self.backup_phone,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class Task(db.Model):
    __tablename__ = "tasks"
    task_id = db.Column(db.String(64), primary_key=True)  # "TASK-xxx"
    status = db.Column(db.String(50), nullable=False, default="assigned")
    platform_order_id = db.Column(
        db.String(64),
        db.ForeignKey("platform_orders.platform_order_id"),
        nullable=True,
        index=True,
    )

    box_no = db.Column(db.String(10), db.ForeignKey("boxes.box_no"), nullable=False)

    courier_openid = db.Column(db.String(64), nullable=True)
    receiver_openid = db.Column(db.String(64), nullable=True, index=True)
    receiver_phone = db.Column(db.String(20), nullable=True)
    merchant_name = db.Column(db.String(100), nullable=True)

    ai_message = db.Column(db.String(255), nullable=True)
    exception_status = db.Column(db.String(20), nullable=False, default="none", index=True)
    exception_note = db.Column(db.String(255), nullable=True)
    package_id = db.Column(db.String(64), db.ForeignKey("packages.package_id"), nullable=True)

    put_in_at = db.Column(db.Integer, nullable=True)
    ai_checked_at = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "taskId": self.task_id,
            "status": self.status,
            "source": "platform_order" if self.platform_order_id else "manual",
            "platformOrderId": self.platform_order_id,
            "boxNo": self.box_no,
            "courierOpenid": self.courier_openid,
            "receiverOpenid": self.receiver_openid,
            "receiverPhone": self.receiver_phone,
            "merchantName": self.merchant_name,
            "aiMessage": self.ai_message,
            "exceptionStatus": self.exception_status or "none",
            "exceptionNote": self.exception_note,
            "hasActiveException": (self.exception_status or "none") == "pending",
            "exceptionResolved": (self.exception_status or "none") == "resolved",
            "packageId": self.package_id,
            "createdAt": int(self.created_at.timestamp()) if self.created_at else None,
        }


class Box(db.Model):
    __tablename__ = "boxes"
    box_no = db.Column(db.String(10), primary_key=True)  # A01...
    status = db.Column(db.String(50), nullable=False, default="empty")  # empty/reserved/occupied
    task_id = db.Column(db.String(64), db.ForeignKey("tasks.task_id"), nullable=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "boxNo": self.box_no,
            "status": self.status,
            "taskId": self.task_id,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class Package(db.Model):
    __tablename__ = "packages"

    package_id = db.Column(db.String(64), primary_key=True)  # "PKG-xxx"

    task_id = db.Column(db.String(64), db.ForeignKey("tasks.task_id"), nullable=False)
    box_no = db.Column(db.String(10), db.ForeignKey("boxes.box_no"), nullable=False)

    receiver_openid = db.Column(db.String(64), nullable=True, index=True)
    receiver_phone = db.Column(db.String(20), nullable=True)
    merchant_name = db.Column(db.String(100), nullable=True)

    pickup_code = db.Column(db.String(6), nullable=False)  # 6位码

    # pending: 已入柜待取 / picked: 已取走（你现有逻辑）
    status = db.Column(db.String(50), nullable=False, default="pending")

    # ✅ 入柜时间（你原来叫 arrived_at，保留，int timestamp）
    arrived_at = db.Column(db.Integer, nullable=True, index=True)

    # ✅ 新增：取件完成时间（int timestamp），用于计算“取件耗时”
    picked_at = db.Column(db.Integer, nullable=True, index=True)

    # ✅ 可选：最后一次提醒时间（用于防止重复提醒/展示）
    last_reminded_at = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "packageId": self.package_id,
            "packageNo": self.package_id,
            "taskId": self.task_id,
            "boxNo": self.box_no,
            "receiverOpenid": self.receiver_openid,
            "receiverPhone": self.receiver_phone,
            "merchantName": self.merchant_name,
            "pickupCode": self.pickup_code,
            "status": self.status,
            "arrivedAt": self.arrived_at,
            "pickedAt": self.picked_at,
            "lastRemindedAt": self.last_reminded_at,
        }

class RemindEvent(db.Model):
    __tablename__ = "remind_events"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    openid = db.Column(db.String(64), nullable=False, index=True)
    package_id = db.Column(db.String(64), nullable=True, index=True)

    # 到点才提醒（UTC 时间）
    due_at = db.Column(db.DateTime, nullable=False, index=True)

    message = db.Column(db.String(255), nullable=False)

    # auto / admin
    source = db.Column(db.String(20), nullable=False, default="auto")

    # 已提醒（前端取走后置为 now）
    sent_at = db.Column(db.DateTime, nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)  # ✅用户在通知中心已读
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "openid": self.openid,
            "packageId": self.package_id,
            "dueAt": self.due_at.isoformat() if self.due_at else None,
            "message": self.message,
            "source": self.source,
            "sentAt": self.sent_at.isoformat() if self.sent_at else None,
            "readAt": self.read_at.isoformat() if self.read_at else None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }

class Courier(db.Model):
    __tablename__ = "couriers"

    courier_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    openid = db.Column(db.String(64), unique=True, nullable=False)
    courier_name = db.Column(db.String(100), nullable=True)
    nickname = db.Column(db.String(100), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="available")
    work_status = db.Column(db.String(20), nullable=False, default="空闲")

    # pending / approved / rejected
    apply_status = db.Column(db.String(20), nullable=False, default="pending")

    # ✅新增：申请提交时间（可选但推荐）
    applied_at = db.Column(db.DateTime, nullable=True)

    # ✅新增：审核信息
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_note = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "courierId": self.courier_id,
            "openid": self.openid,
            "courierName": self.courier_name,
            "courier_name": self.courier_name,
            "nickname": self.nickname,
            "phoneNumber": self.phone_number,
            "status": self.status,
            "workStatus": self.work_status,
            "applyStatus": self.apply_status,
            "appliedAt": self.applied_at.isoformat() if self.applied_at else None,
            "reviewedAt": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewNote": self.review_note,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
    
class PickupHistory(db.Model):
    __tablename__ = "pickup_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    openid = db.Column(db.String(64), nullable=False, index=True)

    # 本次取件耗时（秒）= picked_at - arrived_at
    duration_sec = db.Column(db.Integer, nullable=False)

    # 追溯对应包裹（可选但推荐）
    package_id = db.Column(db.String(64), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class RemindPolicy(db.Model):
    __tablename__ = "remind_policies"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    openid = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # auto / fixed / custom
    mode = db.Column(db.String(20), nullable=False, default="auto")

    # fixed 模式用：1~5
    fixed_count = db.Column(db.Integer, nullable=True)

    # custom 模式用：存 JSON 字符串，例如 "[0,30,60]"
    offsets_json = db.Column(db.String(255), nullable=True)

    enabled = db.Column(db.Boolean, default=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "openid": self.openid,
            "mode": self.mode,
            "fixedCount": self.fixed_count,
            "offsetsJson": self.offsets_json,
            "enabled": bool(self.enabled),
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    
class Admin(db.Model):
    __tablename__ = "admins"

    admin_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    openid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    username = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(100), nullable=True)
    role = db.Column(db.String(50), nullable=False, default="admin")
    password_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "adminId": self.admin_id,
            "openid": self.openid,
            "username": self.username,
            "displayName": self.display_name,
            "role": self.role,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class AdminLog(db.Model):
    __tablename__ = "admin_logs"
    log_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    action = db.Column(db.String(100), nullable=False)

    platform_order_id = db.Column(db.String(64), nullable=True, index=True)
    task_id = db.Column(db.String(64), nullable=True)
    package_id = db.Column(db.String(64), nullable=True, index=True)
    admin_id = db.Column(db.Integer, nullable=True)
    actor_type = db.Column(db.String(20), nullable=True)
    actor_openid = db.Column(db.String(64), nullable=True)
    detail = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "logId": self.log_id,
            "action": self.action,
            "platformOrderId": self.platform_order_id,
            "taskId": self.task_id,
            "packageId": self.package_id,
            "adminId": self.admin_id,
            "actorType": self.actor_type,
            "actorOpenid": self.actor_openid,
            "detail": self.detail,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class LockerCommand(db.Model):
    __tablename__ = "locker_commands"

    command_id = db.Column(db.String(64), primary_key=True)
    box_no = db.Column(db.String(10), db.ForeignKey("boxes.box_no"), nullable=False, index=True)
    task_id = db.Column(db.String(64), db.ForeignKey("tasks.task_id"), nullable=True, index=True)
    package_id = db.Column(db.String(64), db.ForeignKey("packages.package_id"), nullable=True, index=True)

    operator_type = db.Column(db.String(20), nullable=False)
    operator_id = db.Column(db.String(64), nullable=True)
    reason = db.Column(db.String(50), nullable=True)

    mode = db.Column(db.String(20), nullable=False, default="mock", index=True)
    controller_name = db.Column(db.String(100), nullable=True)
    controller_url = db.Column(db.String(255), nullable=True)
    hardware_profile = db.Column(db.String(100), nullable=True)
    power_topology = db.Column(db.String(50), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="created", index=True)
    business_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    accepted = db.Column(db.Boolean, default=False)
    fallback_used = db.Column(db.Boolean, default=False)
    pending_hardware = db.Column(db.Boolean, default=False)

    door_state = db.Column(db.String(20), nullable=True)
    lock_feedback_state = db.Column(db.String(20), nullable=True)
    controller_power_state = db.Column(db.String(20), nullable=True)
    lock_power_state = db.Column(db.String(20), nullable=True)
    last_error = db.Column(db.String(255), nullable=True)
    detail = db.Column(db.String(255), nullable=True)
    business_note = db.Column(db.String(255), nullable=True)
    recording_scene = db.Column(db.String(20), nullable=True, index=True)
    ai_participates = db.Column(db.Boolean, default=False)
    recording_status = db.Column(db.String(30), nullable=True, index=True)
    recording_started_at = db.Column(db.DateTime, nullable=True, index=True)
    recording_stopped_at = db.Column(db.DateTime, nullable=True, index=True)
    video_status = db.Column(db.String(30), nullable=True, index=True)
    video_path = db.Column(db.String(255), nullable=True)
    snapshot_status = db.Column(db.String(30), nullable=True, index=True)
    snapshot_path = db.Column(db.String(255), nullable=True)
    result_image_status = db.Column(db.String(30), nullable=True, index=True)
    result_image_path = db.Column(db.String(255), nullable=True)

    request_payload = db.Column(db.Text, nullable=True)
    last_callback_payload = db.Column(db.Text, nullable=True)

    timeout_at = db.Column(db.DateTime, nullable=True, index=True)
    door_close_timeout_at = db.Column(db.DateTime, nullable=True, index=True)
    accepted_at = db.Column(db.DateTime, nullable=True, index=True)
    relay_triggered_at = db.Column(db.DateTime, nullable=True, index=True)
    lock_opened_at = db.Column(db.DateTime, nullable=True, index=True)
    door_opened_at = db.Column(db.DateTime, nullable=True, index=True)
    door_closed_at = db.Column(db.DateTime, nullable=True, index=True)
    business_completed_at = db.Column(db.DateTime, nullable=True, index=True)
    finished_at = db.Column(db.DateTime, nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def to_dict(self):
        request_payload = self.request_payload
        callback_payload = self.last_callback_payload
        for attr_name, raw in (("request_payload", request_payload), ("last_callback_payload", callback_payload)):
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            if attr_name == "request_payload":
                request_payload = parsed
            else:
                callback_payload = parsed

        return {
            "commandId": self.command_id,
            "boxNo": self.box_no,
            "taskId": self.task_id,
            "packageId": self.package_id,
            "operatorType": self.operator_type,
            "operatorId": self.operator_id,
            "reason": self.reason,
            "mode": self.mode,
            "controllerName": self.controller_name,
            "controllerUrl": self.controller_url,
            "hardwareProfile": self.hardware_profile,
            "powerTopology": self.power_topology,
            "status": self.status,
            "businessStatus": self.business_status,
            "accepted": bool(self.accepted),
            "fallbackUsed": bool(self.fallback_used),
            "pendingHardware": bool(self.pending_hardware),
            "doorState": self.door_state,
            "lockFeedbackState": self.lock_feedback_state,
            "controllerPowerState": self.controller_power_state,
            "lockPowerState": self.lock_power_state,
            "lastError": self.last_error,
            "detail": self.detail,
            "businessNote": self.business_note,
            "recordingScene": self.recording_scene,
            "aiParticipates": bool(self.ai_participates),
            "recordingStatus": self.recording_status,
            "recordingStartedAt": self.recording_started_at.isoformat() if self.recording_started_at else None,
            "recordingStoppedAt": self.recording_stopped_at.isoformat() if self.recording_stopped_at else None,
            "videoStatus": self.video_status,
            "videoPath": self.video_path,
            "snapshotStatus": self.snapshot_status,
            "snapshotPath": self.snapshot_path,
            "resultImageStatus": self.result_image_status,
            "resultImagePath": self.result_image_path,
            "openSucceeded": bool(self.lock_opened_at or self.door_opened_at or self.door_closed_at),
            "pickupClosedLoopCompleted": bool(self.door_opened_at and self.door_closed_at),
            "requestPayload": request_payload,
            "lastCallbackPayload": callback_payload,
            "timeoutAt": self.timeout_at.isoformat() if self.timeout_at else None,
            "doorCloseTimeoutAt": self.door_close_timeout_at.isoformat() if self.door_close_timeout_at else None,
            "acceptedAt": self.accepted_at.isoformat() if self.accepted_at else None,
            "relayTriggeredAt": self.relay_triggered_at.isoformat() if self.relay_triggered_at else None,
            "lockOpenedAt": self.lock_opened_at.isoformat() if self.lock_opened_at else None,
            "doorOpenedAt": self.door_opened_at.isoformat() if self.door_opened_at else None,
            "doorClosedAt": self.door_closed_at.isoformat() if self.door_closed_at else None,
            "businessCompletedAt": self.business_completed_at.isoformat() if self.business_completed_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class LockerEvent(db.Model):
    __tablename__ = "locker_events"

    event_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    command_id = db.Column(db.String(64), db.ForeignKey("locker_commands.command_id"), nullable=True, index=True)
    box_no = db.Column(db.String(10), db.ForeignKey("boxes.box_no"), nullable=False, index=True)

    event_type = db.Column(db.String(50), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, default="system", index=True)
    command_status = db.Column(db.String(30), nullable=True, index=True)
    door_state = db.Column(db.String(20), nullable=True)
    lock_feedback_state = db.Column(db.String(20), nullable=True)
    controller_power_state = db.Column(db.String(20), nullable=True)
    lock_power_state = db.Column(db.String(20), nullable=True)
    detail = db.Column(db.String(255), nullable=True)
    raw_payload = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        raw_payload = self.raw_payload
        if raw_payload:
            try:
                raw_payload = json.loads(raw_payload)
            except Exception:
                pass

        return {
            "eventId": self.event_id,
            "commandId": self.command_id,
            "boxNo": self.box_no,
            "eventType": self.event_type,
            "source": self.source,
            "commandStatus": self.command_status,
            "doorState": self.door_state,
            "lockFeedbackState": self.lock_feedback_state,
            "controllerPowerState": self.controller_power_state,
            "lockPowerState": self.lock_power_state,
            "detail": self.detail,
            "rawPayload": raw_payload,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }
