from sqlalchemy import inspect, text

from extensions import db


def _table_exists(table_name: str) -> bool:
    inspector = inspect(db.engine)
    return table_name in inspector.get_table_names()


def _get_column_names(table_name: str):
    if not _table_exists(table_name):
        return set()
    inspector = inspect(db.engine)
    return {col["name"] for col in inspector.get_columns(table_name)}


def ensure_runtime_schema():
    """
    Keep the existing SQLite database usable without introducing Alembic yet.
    """
    db.create_all()

    user_columns = _get_column_names("users")
    if "receiver_name" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN receiver_name VARCHAR(100)"))
    if "pickup_area" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN pickup_area VARCHAR(100)"))
    if "backup_phone" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN backup_phone VARCHAR(20)"))

    admin_columns = _get_column_names("admins")
    if admin_columns:
        if "display_name" not in admin_columns:
            db.session.execute(text("ALTER TABLE admins ADD COLUMN display_name VARCHAR(100)"))
        if "password_hash" not in admin_columns:
            db.session.execute(text("ALTER TABLE admins ADD COLUMN password_hash VARCHAR(255)"))

    courier_columns = _get_column_names("couriers")
    if "courier_name" not in courier_columns:
        db.session.execute(text("ALTER TABLE couriers ADD COLUMN courier_name VARCHAR(100)"))
    if "nickname" not in courier_columns:
        db.session.execute(text("ALTER TABLE couriers ADD COLUMN nickname VARCHAR(100)"))
    if "work_status" not in courier_columns:
        db.session.execute(text("ALTER TABLE couriers ADD COLUMN work_status VARCHAR(20) DEFAULT '空闲'"))

    task_columns = _get_column_names("tasks")
    if "platform_order_id" not in task_columns:
        db.session.execute(text("ALTER TABLE tasks ADD COLUMN platform_order_id VARCHAR(64)"))
    if "receiver_openid" not in task_columns:
        db.session.execute(text("ALTER TABLE tasks ADD COLUMN receiver_openid VARCHAR(64)"))
    if "exception_status" not in task_columns:
        db.session.execute(text("ALTER TABLE tasks ADD COLUMN exception_status VARCHAR(20) DEFAULT 'none'"))
    if "exception_note" not in task_columns:
        db.session.execute(text("ALTER TABLE tasks ADD COLUMN exception_note VARCHAR(255)"))

    platform_order_columns = _get_column_names("platform_orders")
    if "closed_at" not in platform_order_columns:
        db.session.execute(text("ALTER TABLE platform_orders ADD COLUMN closed_at DATETIME"))
    if "status_note" not in platform_order_columns:
        db.session.execute(text("ALTER TABLE platform_orders ADD COLUMN status_note VARCHAR(255)"))
    if "qr_token" not in platform_order_columns:
        db.session.execute(text("ALTER TABLE platform_orders ADD COLUMN qr_token VARCHAR(64)"))
    if "qr_image_path" not in platform_order_columns:
        db.session.execute(text("ALTER TABLE platform_orders ADD COLUMN qr_image_path VARCHAR(255)"))
    if "verify_code" not in platform_order_columns:
        db.session.execute(text("ALTER TABLE platform_orders ADD COLUMN verify_code VARCHAR(12)"))

    admin_log_columns = _get_column_names("admin_logs")
    if "platform_order_id" not in admin_log_columns:
        db.session.execute(text("ALTER TABLE admin_logs ADD COLUMN platform_order_id VARCHAR(64)"))
    if "package_id" not in admin_log_columns:
        db.session.execute(text("ALTER TABLE admin_logs ADD COLUMN package_id VARCHAR(64)"))
    if "actor_type" not in admin_log_columns:
        db.session.execute(text("ALTER TABLE admin_logs ADD COLUMN actor_type VARCHAR(20)"))
    if "actor_openid" not in admin_log_columns:
        db.session.execute(text("ALTER TABLE admin_logs ADD COLUMN actor_openid VARCHAR(64)"))
    if "detail" not in admin_log_columns:
        db.session.execute(text("ALTER TABLE admin_logs ADD COLUMN detail VARCHAR(255)"))

    locker_command_columns = _get_column_names("locker_commands")
    if locker_command_columns and "business_status" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN business_status VARCHAR(30) DEFAULT 'pending'"))
    if locker_command_columns and "business_note" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN business_note VARCHAR(255)"))
    if locker_command_columns and "business_completed_at" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN business_completed_at DATETIME"))
    if locker_command_columns and "recording_scene" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN recording_scene VARCHAR(20)"))
    if locker_command_columns and "ai_participates" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN ai_participates BOOLEAN DEFAULT 0"))
    if locker_command_columns and "recording_status" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN recording_status VARCHAR(30)"))
    if locker_command_columns and "recording_started_at" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN recording_started_at DATETIME"))
    if locker_command_columns and "recording_stopped_at" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN recording_stopped_at DATETIME"))
    if locker_command_columns and "video_status" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN video_status VARCHAR(30)"))
    if locker_command_columns and "video_path" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN video_path VARCHAR(255)"))
    if locker_command_columns and "snapshot_status" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN snapshot_status VARCHAR(30)"))
    if locker_command_columns and "snapshot_path" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN snapshot_path VARCHAR(255)"))
    if locker_command_columns and "result_image_status" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN result_image_status VARCHAR(30)"))
    if locker_command_columns and "result_image_path" not in locker_command_columns:
        db.session.execute(text("ALTER TABLE locker_commands ADD COLUMN result_image_path VARCHAR(255)"))

    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_tasks_platform_order_id ON tasks (platform_order_id)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_tasks_receiver_openid ON tasks (receiver_openid)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_tasks_exception_status ON tasks (exception_status)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_platform_orders_closed_at ON platform_orders (closed_at)"
    ))
    db.session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_orders_qr_token ON platform_orders (qr_token)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_admin_logs_platform_order_id ON admin_logs (platform_order_id)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_admin_logs_package_id ON admin_logs (package_id)"
    ))
    if _table_exists("locker_commands"):
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_box_no ON locker_commands (box_no)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_status ON locker_commands (status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_business_status ON locker_commands (business_status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_recording_scene ON locker_commands (recording_scene)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_recording_status ON locker_commands (recording_status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_video_status ON locker_commands (video_status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_snapshot_status ON locker_commands (snapshot_status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_result_image_status ON locker_commands (result_image_status)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_timeout_at ON locker_commands (timeout_at)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_commands_door_close_timeout_at ON locker_commands (door_close_timeout_at)"
        ))

    if _table_exists("locker_events"):
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_events_box_no ON locker_events (box_no)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_events_command_id ON locker_events (command_id)"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_locker_events_event_type ON locker_events (event_type)"
        ))
    db.session.commit()
