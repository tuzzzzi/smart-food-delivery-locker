from flask import current_app


def _normalize_locker_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"hardware", "real", "reserved", "placeholder"}:
        return "hardware"
    return "mock"


def _normalize_ai_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"local", "yolov5", "script", "real", "mock"}:
        return "local"
    return value or "local"


def _mode_label(mode: str) -> str:
    mapping = {
        "mock": "演示模式",
        "hardware": "设备模式",
        "reserved": "待接入",
        "real": "设备模式",
        "local": "本地 YOLOv5",
        "yolov5": "本地 YOLOv5",
        "script": "本地 YOLOv5",
    }
    value = (mode or "").strip().lower()
    return mapping.get(value, mode or "unknown")


def demo_fallback_enabled() -> bool:
    return bool(current_app.config.get("DEMO_INTERFACE_FALLBACK", True))


def get_platform_integration_config():
    mode = current_app.config.get("PLATFORM_PUSH_MODE", "mock")
    return {
        "key": "platform",
        "label": "外卖平台接入",
        "mode": mode,
        "modeLabel": _mode_label(mode),
        "providerName": current_app.config.get("PLATFORM_PROVIDER_NAME", "generic"),
        "pushPath": current_app.config.get("PLATFORM_PUSH_PATH", "/api/integration/platform/push"),
        "pushSecretConfigured": bool(current_app.config.get("PLATFORM_PUSH_SECRET")),
        "callbackUrl": current_app.config.get("PLATFORM_CALLBACK_URL", ""),
        "timeoutMs": current_app.config.get("PLATFORM_TIMEOUT_MS", 3000),
        "summary": (
            "当前支持后台补录与演示接单，后续可平滑接入外部平台。"
            if mode == "mock"
            else "平台接入已启用，订单可进入正常派单流程。"
        ),
    }


def get_locker_integration_config():
    raw_mode = current_app.config.get("LOCKER_CONTROL_MODE", "mock")
    mode = _normalize_locker_mode(raw_mode)
    return {
        "key": "locker",
        "label": "柜门控制服务",
        "mode": mode,
        "rawMode": raw_mode,
        "modeLabel": _mode_label(mode),
        "providerName": current_app.config.get("LOCKER_CONTROLLER_NAME", "generic-locker"),
        "controllerUrl": current_app.config.get("LOCKER_CONTROLLER_URL", ""),
        "callbackPath": current_app.config.get("LOCKER_CALLBACK_PATH", "/api/integration/locker/callback"),
        "timeoutMs": current_app.config.get("LOCKER_TIMEOUT_MS", 3000),
        "openTimeoutMs": current_app.config.get("LOCKER_OPEN_TIMEOUT_MS", 15000),
        "doorCloseTimeoutMs": current_app.config.get("LOCKER_DOOR_CLOSE_TIMEOUT_MS", 30000),
        "hardwareProfile": current_app.config.get("LOCKER_HARDWARE_PROFILE", "single_door_esp32_cam_split_power"),
        "powerTopology": current_app.config.get("LOCKER_POWER_TOPOLOGY", "split_power"),
        "lockType": current_app.config.get("LOCKER_LOCK_TYPE", "12v_electric_lock_with_feedback"),
        "lockControl": current_app.config.get("LOCKER_LOCK_CONTROL", "single_relay"),
        "doorSensorType": current_app.config.get("LOCKER_DOOR_SENSOR_TYPE", "wired_door_magnet"),
        "lockPowerSource": current_app.config.get("LOCKER_LOCK_POWER_SOURCE", "3s_18650_battery_pack"),
        "controllerPowerSource": current_app.config.get("LOCKER_CONTROLLER_POWER_SOURCE", "usb_power_bank"),
        "lockFeedbackEnabled": bool(current_app.config.get("LOCKER_LOCK_FEEDBACK_ENABLED", True)),
        "doorSensorEnabled": bool(current_app.config.get("LOCKER_DOOR_SENSOR_ENABLED", True)),
        "sharedPowerRail": bool(current_app.config.get("LOCKER_SHARED_POWER_RAIL", False)),
        "mockAutoComplete": bool(current_app.config.get("LOCKER_MOCK_AUTO_COMPLETE", True)),
        "summary": (
            "当前以演示方式完成柜门闭环展示，设备反馈链路已保留诊断入口。"
            if mode == "mock"
            else "柜门控制服务已启用，正在接收设备反馈并同步业务状态。"
        ),
    }


def get_ai_integration_config():
    raw_mode = current_app.config.get("AI_DETECTION_MODE", "local")
    mode = _normalize_ai_mode(raw_mode)
    return {
        "key": "ai",
        "label": "图像确认服务",
        "mode": mode,
        "rawMode": raw_mode,
        "modeLabel": _mode_label(mode),
        "providerName": current_app.config.get("AI_PROVIDER_NAME", "local-yolov5"),
        "serviceUrl": current_app.config.get("AI_SERVICE_URL", ""),
        "cameraStreamUrl": current_app.config.get("CAMERA_STREAM_URL", ""),
        "callbackPath": current_app.config.get("AI_CALLBACK_PATH", "/api/integration/ai/callback"),
        "timeoutMs": current_app.config.get("AI_TIMEOUT_MS", 15000),
        "summary": (
            "图像确认服务已启用，当前直接使用本地 YOLOv5 对当前任务投递现场图进行检测。"
            if mode == "local"
            else "图像确认服务已启用，入柜结果会进入系统确认流程。"
        ),
    }


def get_integration_status_cards():
    return [
        get_platform_integration_config(),
        get_locker_integration_config(),
        get_ai_integration_config(),
    ]


def get_integration_status_payload():
    return {
        "demoFallbackEnabled": demo_fallback_enabled(),
        "items": get_integration_status_cards(),
    }
