import os
import secrets

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(REPO_ROOT, ".env"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    APP_ID = os.getenv("WX_APP_ID", "")
    APP_SECRET = os.getenv("WX_APP_SECRET", "")
    USE_MOCK = _env_bool("USE_MOCK", True)

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'database.db')}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600

    # Keep the current demo path available even after reservation hooks are added.
    DEMO_INTERFACE_FALLBACK = _env_bool("DEMO_INTERFACE_FALLBACK", False)
    DEMO_DEFAULT_RECEIVER_OPENID = os.getenv("DEMO_DEFAULT_RECEIVER_OPENID", "").strip()

    # Platform order push / callback reservation
    PLATFORM_PUSH_MODE = os.getenv("PLATFORM_PUSH_MODE", "mock").strip().lower() or "mock"
    PLATFORM_PROVIDER_NAME = os.getenv("PLATFORM_PROVIDER_NAME", "generic").strip() or "generic"
    PLATFORM_PUSH_SECRET = os.getenv("PLATFORM_PUSH_SECRET", "").strip()
    PLATFORM_PUSH_PATH = os.getenv("PLATFORM_PUSH_PATH", "/api/integration/platform/push").strip() or "/api/integration/platform/push"
    PLATFORM_CALLBACK_URL = os.getenv("PLATFORM_CALLBACK_URL", "").strip()
    PLATFORM_TIMEOUT_MS = _env_int("PLATFORM_TIMEOUT_MS", 3000)

    # Locker / door controller reservation
    LOCKER_CONTROL_MODE = os.getenv("LOCKER_CONTROL_MODE", "mock").strip().lower() or "mock"
    LOCKER_CONTROLLER_NAME = os.getenv("LOCKER_CONTROLLER_NAME", "generic-locker").strip() or "generic-locker"
    LOCKER_CONTROLLER_URL = os.getenv("LOCKER_CONTROLLER_URL", "").strip()
    LOCKER_CALLBACK_PATH = os.getenv("LOCKER_CALLBACK_PATH", "/api/integration/locker/callback").strip() or "/api/integration/locker/callback"
    LOCKER_TIMEOUT_MS = _env_int("LOCKER_TIMEOUT_MS", 3000)
    LOCKER_OPEN_TIMEOUT_MS = _env_int("LOCKER_OPEN_TIMEOUT_MS", 15000)
    LOCKER_DOOR_CLOSE_TIMEOUT_MS = _env_int("LOCKER_DOOR_CLOSE_TIMEOUT_MS", 30000)
    LOCKER_HARDWARE_PROFILE = os.getenv(
        "LOCKER_HARDWARE_PROFILE",
        "single_door_esp32_cam_split_power",
    ).strip() or "single_door_esp32_cam_split_power"
    LOCKER_POWER_TOPOLOGY = os.getenv("LOCKER_POWER_TOPOLOGY", "split_power").strip() or "split_power"
    LOCKER_LOCK_TYPE = os.getenv("LOCKER_LOCK_TYPE", "12v_electric_lock_with_feedback").strip() or "12v_electric_lock_with_feedback"
    LOCKER_LOCK_CONTROL = os.getenv("LOCKER_LOCK_CONTROL", "single_relay").strip() or "single_relay"
    LOCKER_DOOR_SENSOR_TYPE = os.getenv("LOCKER_DOOR_SENSOR_TYPE", "wired_door_magnet").strip() or "wired_door_magnet"
    LOCKER_LOCK_POWER_SOURCE = os.getenv("LOCKER_LOCK_POWER_SOURCE", "3s_18650_battery_pack").strip() or "3s_18650_battery_pack"
    LOCKER_CONTROLLER_POWER_SOURCE = os.getenv("LOCKER_CONTROLLER_POWER_SOURCE", "usb_power_bank").strip() or "usb_power_bank"
    LOCKER_LOCK_FEEDBACK_ENABLED = _env_bool("LOCKER_LOCK_FEEDBACK_ENABLED", True)
    LOCKER_DOOR_SENSOR_ENABLED = _env_bool("LOCKER_DOOR_SENSOR_ENABLED", True)
    LOCKER_SHARED_POWER_RAIL = _env_bool("LOCKER_SHARED_POWER_RAIL", False)
    LOCKER_MOCK_AUTO_COMPLETE = _env_bool("LOCKER_MOCK_AUTO_COMPLETE", True)

    # Camera / AI detection reservation
    AI_DETECTION_MODE = os.getenv("AI_DETECTION_MODE", "local").strip().lower() or "local"
    AI_PROVIDER_NAME = os.getenv("AI_PROVIDER_NAME", "local-yolov5").strip() or "local-yolov5"
    AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "").strip()
    AI_CALLBACK_PATH = os.getenv("AI_CALLBACK_PATH", "/api/integration/ai/callback").strip() or "/api/integration/ai/callback"
    CAMERA_STREAM_URL = os.getenv("CAMERA_STREAM_URL", "").strip()
    AI_TIMEOUT_MS = _env_int("AI_TIMEOUT_MS", 15000)
    AI_PYTHON_BIN = os.getenv("AI_PYTHON_BIN", "python").strip() or "python"
    AI_YOLO_ROOT = os.getenv(
        "AI_YOLO_ROOT",
        os.path.join(REPO_ROOT, "ai", "yolov5"),
    ).strip()
    AI_LOCAL_SCRIPT = os.getenv(
        "AI_LOCAL_SCRIPT",
        os.path.join(REPO_ROOT, "ai", "inference", "detect_snapshot.py"),
    ).strip()
    AI_LOCAL_WEIGHTS = (
        os.getenv("AI_LOCAL_WEIGHTS")
        or os.getenv("AI_MODEL_PATH")
        or os.path.join(
            REPO_ROOT,
            "ai",
            "runtime",
            "train_runs",
            "packing_finetune_esp32",
            "weights",
            "best.pt",
        )
    ).strip()
    AI_CONFIDENCE_THRESHOLD = os.getenv("AI_CONFIDENCE_THRESHOLD", "0.25").strip() or "0.25"
    AI_IMAGE_SIZE = _env_int("AI_IMAGE_SIZE", 640)
    ARTIFACTS_DIR = os.getenv(
        "ARTIFACTS_DIR",
        os.path.join(REPO_ROOT, "artifacts"),
    ).strip()
    ARTIFACTS_URL_PREFIX = os.getenv("ARTIFACTS_URL_PREFIX", "/artifacts").strip() or "/artifacts"
