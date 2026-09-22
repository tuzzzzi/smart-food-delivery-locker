#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <time.h>
#include "esp_camera.h"

#include "config.local.h"
const char* BOX_NO = "A01";

const int RELAY_PIN = 14;
const int DOOR_SENSOR_PIN = 13;

const int RELAY_ACTIVE_LEVEL = HIGH;
const int RELAY_IDLE_LEVEL = LOW;

const int DOOR_OPEN_LEVEL = HIGH;
const int DOOR_CLOSED_LEVEL = LOW;

const unsigned long WIFI_CONNECT_TIMEOUT_MS = 5000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 2000;
const unsigned long POLL_HTTP_TIMEOUT_MS = 1500;
const unsigned long EVENT_HTTP_TIMEOUT_MS = 2000;
const unsigned long UPLOAD_HTTP_TIMEOUT_MS = 1500;
const unsigned long RELAY_PULSE_MS = 1000;
const unsigned long IDLE_POLL_INTERVAL_MS = 1000;
const unsigned long FAST_POLL_INTERVAL_MS = 250;
const unsigned long FAST_POLL_WINDOW_MS = 8000;
const unsigned long POLL_FAIL_RETRY_MS = 300;
const unsigned long MALFORMED_POLL_RETRY_MS = 150;
const unsigned long COMMAND_CANDIDATE_COOLDOWN_MS = 250;
const unsigned long RETRY_INTERVAL_MS = 1000;
const unsigned long DOOR_DEBOUNCE_MS = 80;
const unsigned long DOOR_OPEN_HOLD_MS = 200;
const unsigned long POLL_SKIP_LOG_THROTTLE_MS = 1000;
const unsigned long SNAPSHOT_DISCARD_FRAME_DELAY_MS = 80;

// Camera pin map: ESP32-CAM / OV3660 style module.
// If your board pinout is different, only adjust these definitions.
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

Preferences devicePrefs;

String deviceId;

String activeCommandId = "";
String activeOperatorType = "";
String activeScene = "";
String activeTaskId = "";
String activePackageId = "";
String lastExecutedCommandId = "";
String lastPollSkipReason = "";
String nextPollGateReason = "boot";

bool commandActive = false;
bool relayPulsed = false;
bool relayPulseActive = false;
bool lockOpenedSent = false;
bool doorOpenedSent = false;
bool doorClosedSent = false;
bool wifiConnectInFlight = false;
bool cameraReady = false;

unsigned long lastPollMs = 0;
unsigned long lastActualPollStartMs = 0;
unsigned long lastPollSkipLogMs = 0;
unsigned long nextPollAllowedMs = 0;
unsigned long fastPollUntilMs = 0;
unsigned long relayPulseStartedMs = 0;
unsigned long lastLockOpenedAttemptMs = 0;
unsigned long lastDoorOpenedAttemptMs = 0;
unsigned long lastDoorClosedAttemptMs = 0;
unsigned long snapshotUploadStartedMs = 0;
unsigned long doorOpenedAtMs = 0;
unsigned long wifiConnectStartedMs = 0;
unsigned long lastWiFiConnectAttemptMs = 0;
unsigned long snapshotCaptureSerial = 0;

int lastDoorRaw = DOOR_CLOSED_LEVEL;
int stableDoorRaw = DOOR_CLOSED_LEVEL;
unsigned long lastDoorChangeMs = 0;
wl_status_t lastWiFiStatus = WL_IDLE_STATUS;

String buildUrl(const String& path) {
  return String(BASE_URL) + path;
}

String toLowerTrimmed(String value) {
  value.trim();
  value.toLowerCase();
  return value;
}

bool isTruthyValue(const String& value) {
  String normalized = toLowerTrimmed(value);
  return normalized == "true" || normalized == "1" || normalized == "yes";
}

String currentTimestamp() {
  time_t now = time(nullptr);
  if (now < 1700000000) {
    return "clock_unsynced";
  }

  struct tm timeinfo;
  if (!gmtime_r(&now, &timeinfo)) {
    return "clock_invalid";
  }

  char buffer[32];
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buffer);
}

const char* wifiStatusText(wl_status_t status) {
  switch (status) {
    case WL_CONNECTED:
      return "connected";
    case WL_NO_SSID_AVAIL:
      return "no_ssid";
    case WL_CONNECT_FAILED:
      return "connect_failed";
    case WL_CONNECTION_LOST:
      return "connection_lost";
    case WL_DISCONNECTED:
      return "disconnected";
    case WL_IDLE_STATUS:
      return "idle";
    case WL_SCAN_COMPLETED:
      return "scan_completed";
    default:
      return "unknown";
  }
}

bool inFastPollWindow() {
  return fastPollUntilMs != 0 && static_cast<long>(fastPollUntilMs - millis()) > 0;
}

unsigned long currentPollIntervalMs() {
  return inFastPollWindow() ? FAST_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
}

void markFastPollWindow(const char* reason) {
  fastPollUntilMs = millis() + FAST_POLL_WINDOW_MS;
  Serial.printf("[poll fast] reason=%s intervalMs=%lu windowMs=%lu\n", reason ? reason : "-", FAST_POLL_INTERVAL_MS, FAST_POLL_WINDOW_MS);
}

void clearPollSkipReason() {
  lastPollSkipReason = "";
  lastPollSkipLogMs = 0;
}

void scheduleNextPollAfter(unsigned long delayMs, const char* reason) {
  nextPollAllowedMs = millis() + delayMs;
  nextPollGateReason = reason ? reason : "poll_wait";
}

void logPollSkip(const char* reason, const String& detail = "") {
  String reasonKey = String(reason ? reason : "");
  unsigned long nowMs = millis();
  if (reasonKey == lastPollSkipReason && nowMs - lastPollSkipLogMs < POLL_SKIP_LOG_THROTTLE_MS) {
    return;
  }

  lastPollSkipReason = reasonKey;
  lastPollSkipLogMs = nowMs;

  String timestamp = currentTimestamp();
  if (detail.length() > 0) {
    Serial.printf("[poll skipped] ts=%s millis=%lu reason=%s detail=%s\n",
      timestamp.c_str(),
      nowMs,
      reasonKey.c_str(),
      detail.c_str());
  } else {
    Serial.printf("[poll skipped] ts=%s millis=%lu reason=%s\n",
      timestamp.c_str(),
      nowMs,
      reasonKey.c_str());
  }
}

void resetDoorDebounceState() {
  lastDoorRaw = digitalRead(DOOR_SENSOR_PIN);
  stableDoorRaw = lastDoorRaw;
  lastDoorChangeMs = millis();
}

void forceRelayIdle(const char* reason) {
  digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);
  if (relayPulseActive) {
    relayPulseActive = false;
    Serial.printf("[relay off] reason=%s\n", reason ? reason : "-");
  }
}

void loadLastExecutedCommandId() {
  devicePrefs.begin("lockercmd", false);
  lastExecutedCommandId = devicePrefs.getString("lastcmd", "");
  if (lastExecutedCommandId.length() > 0) {
    Serial.printf("[dedup] loaded lastExecutedCommandId=%s\n", lastExecutedCommandId.c_str());
  } else {
    Serial.println("[dedup] no lastExecutedCommandId stored");
  }
}

void rememberLastExecutedCommandId(const String& commandId) {
  if (commandId.length() == 0 || commandId == lastExecutedCommandId) {
    return;
  }

  lastExecutedCommandId = commandId;
  devicePrefs.putString("lastcmd", commandId);
  Serial.printf("[dedup] remembered lastExecutedCommandId=%s\n", lastExecutedCommandId.c_str());
}

void beginWiFiConnect(const char* reason, bool force = false) {
  unsigned long nowMs = millis();
  if (!force && nowMs - lastWiFiConnectAttemptMs < WIFI_RETRY_INTERVAL_MS) {
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  wifiConnectInFlight = true;
  wifiConnectStartedMs = nowMs;
  lastWiFiConnectAttemptMs = nowMs;

  String timestamp = currentTimestamp();
  Serial.printf("[wifi] connect_start ts=%s millis=%lu reason=%s ssid=%s\n",
    timestamp.c_str(),
    nowMs,
    reason ? reason : "-",
    WIFI_SSID);
}

void serviceWiFiConnection() {
  wl_status_t status = WiFi.status();
  unsigned long nowMs = millis();
  wl_status_t previousStatus = lastWiFiStatus;

  if (status != lastWiFiStatus) {
    String timestamp = currentTimestamp();
    Serial.printf("[wifi] status_change ts=%s millis=%lu from=%s to=%s\n",
      timestamp.c_str(),
      nowMs,
      wifiStatusText(lastWiFiStatus),
      wifiStatusText(status));
    lastWiFiStatus = status;
  }

  if (status == WL_CONNECTED) {
    if (wifiConnectInFlight || previousStatus != WL_CONNECTED) {
      String timestamp = currentTimestamp();
      Serial.printf("[wifi] connected ts=%s millis=%lu ip=%s connectMs=%lu\n",
        timestamp.c_str(),
        nowMs,
        WiFi.localIP().toString().c_str(),
        wifiConnectStartedMs == 0 ? 0 : nowMs - wifiConnectStartedMs);
      wifiConnectInFlight = false;
      wifiConnectStartedMs = 0;
      markFastPollWindow("wifi_connected");
      lastPollMs = nowMs;
      nextPollAllowedMs = nowMs;
      nextPollGateReason = "wifi_connected_ready";
    }
    return;
  }

  if (wifiConnectInFlight && nowMs - wifiConnectStartedMs >= WIFI_CONNECT_TIMEOUT_MS) {
    String timestamp = currentTimestamp();
    Serial.printf("[wifi] connect_timeout ts=%s millis=%lu waitedMs=%lu status=%s\n",
      timestamp.c_str(),
      nowMs,
      nowMs - wifiConnectStartedMs,
      wifiStatusText(status));
    wifiConnectInFlight = false;
  }

  if (!wifiConnectInFlight && nowMs - lastWiFiConnectAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
    beginWiFiConnect("background_retry");
  }
}

bool ensureWiFiReady() {
  serviceWiFiConnection();
  return WiFi.status() == WL_CONNECTED;
}

String getDeviceId() {
  uint64_t chipId = ESP.getEfuseMac();
  char buf[32];
  snprintf(buf, sizeof(buf), "esp32-cam-%04X%08X", (uint16_t)(chipId >> 32), (uint32_t)chipId);
  return String(buf);
}

String nextSnapshotCaptureId(const String& commandId) {
  snapshotCaptureSerial++;
  return commandId + "-" + String(millis()) + "-" + String(snapshotCaptureSerial);
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_SXGA;
  config.jpeg_quality = 18;
  config.fb_count = 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.print("[camera] init failed: 0x");
    Serial.println(err, HEX);
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor) {
    sensor->set_framesize(sensor, FRAMESIZE_SXGA);
    sensor->set_brightness(sensor, 1);
    sensor->set_saturation(sensor, -1);
  }

  Serial.println("[camera] ready frame=SXGA resolution=1280x1024 fb_count=1 xclk=10000000 quality=18");
  return true;
}

bool readDoorOpenStable() {
  int raw = digitalRead(DOOR_SENSOR_PIN);

  if (raw != lastDoorRaw) {
    lastDoorRaw = raw;
    lastDoorChangeMs = millis();
  }

  if (millis() - lastDoorChangeMs >= DOOR_DEBOUNCE_MS) {
    stableDoorRaw = raw;
  }

  return stableDoorRaw == DOOR_OPEN_LEVEL;
}

void serviceRelayPulse() {
  if (!relayPulseActive) {
    return;
  }

  if (millis() - relayPulseStartedMs >= RELAY_PULSE_MS) {
    forceRelayIdle("pulse_complete");
  }
}

void beginRelayPulse() {
  if (relayPulseActive || relayPulsed || activeCommandId.length() == 0) {
    return;
  }

  rememberLastExecutedCommandId(activeCommandId);
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LEVEL);
  relayPulseStartedMs = millis();
  relayPulseActive = true;
  relayPulsed = true;
  Serial.printf("[relay on] commandId=%s millis=%lu\n", activeCommandId.c_str(), relayPulseStartedMs);
}

String extractJsonObject(const String& json, const String& key) {
  String marker = "\"" + key + "\":";
  int keyIndex = json.indexOf(marker);
  if (keyIndex < 0) return "";

  int start = keyIndex + marker.length();
  while (start < json.length() && (json.charAt(start) == ' ' || json.charAt(start) == '\n' || json.charAt(start) == '\r')) {
    start++;
  }
  if (start >= json.length() || json.charAt(start) != '{') return "";

  int depth = 0;
  for (int i = start; i < json.length(); i++) {
    char ch = json.charAt(i);
    if (ch == '{') depth++;
    if (ch == '}') {
      depth--;
      if (depth == 0) {
        return json.substring(start, i + 1);
      }
    }
  }
  return "";
}

String extractJsonRawValue(const String& json, const String& key) {
  String marker = "\"" + key + "\":";
  int keyIndex = json.indexOf(marker);
  if (keyIndex < 0) return "";

  int valueIndex = keyIndex + marker.length();
  while (valueIndex < json.length() && (json.charAt(valueIndex) == ' ' || json.charAt(valueIndex) == '\n' || json.charAt(valueIndex) == '\r')) {
    valueIndex++;
  }

  if (valueIndex >= json.length()) return "";

  char first = json.charAt(valueIndex);
  if (first == '{') {
    return extractJsonObject(json, key);
  }
  if (first == '\"') {
    return extractJsonValue(json, key);
  }

  int endIndex = valueIndex;
  while (endIndex < json.length()) {
    char ch = json.charAt(endIndex);
    if (ch == ',' || ch == '}') break;
    endIndex++;
  }

  String value = json.substring(valueIndex, endIndex);
  value.trim();
  return value;
}

String extractJsonValue(const String& json, const String& key) {
  String marker = "\"" + key + "\":";
  int keyIndex = json.indexOf(marker);
  if (keyIndex < 0) return "";

  int valueIndex = keyIndex + marker.length();
  while (valueIndex < json.length() && (json.charAt(valueIndex) == ' ' || json.charAt(valueIndex) == '\n' || json.charAt(valueIndex) == '\r')) {
    valueIndex++;
  }

  if (valueIndex >= json.length()) return "";

  if (json.charAt(valueIndex) == '\"') {
    valueIndex++;
    String value = "";
    for (int i = valueIndex; i < json.length(); i++) {
      char ch = json.charAt(i);
      if (ch == '\\' && i + 1 < json.length()) {
        value += json.charAt(i + 1);
        i++;
        continue;
      }
      if (ch == '\"') {
        return value;
      }
      value += ch;
    }
    return value;
  }

  int endIndex = valueIndex;
  while (endIndex < json.length()) {
    char ch = json.charAt(endIndex);
    if (ch == ',' || ch == '}') break;
    endIndex++;
  }
  String value = json.substring(valueIndex, endIndex);
  value.trim();
  return value;
}

bool extractJsonBoolValue(const String& json, const String& key, bool defaultValue = false) {
  String value = extractJsonValue(json, key);
  if (value.length() == 0) {
    return defaultValue;
  }
  return isTruthyValue(value);
}

String deriveScene(const String& operatorType, const String& sceneValue) {
  String normalizedScene = toLowerTrimmed(sceneValue);
  if (normalizedScene.length() > 0) return normalizedScene;

  String normalizedOperator = toLowerTrimmed(operatorType);
  if (normalizedOperator == "courier") return "deposit";
  if (normalizedOperator == "user") return "pickup";
  return "manual";
}

bool isExecutableCommandStatus(const String& statusValue) {
  String normalized = toLowerTrimmed(statusValue);
  return normalized == "accepted" || normalized == "created";
}

bool postLockerEvent(const char* eventType, const char* detail, const char* doorState = nullptr, const char* lockFeedback = nullptr) {
  if (!ensureWiFiReady() || activeCommandId.length() == 0) {
    return false;
  }

  HTTPClient http;
  http.begin(buildUrl("/api/integration/locker/callback"));
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(EVENT_HTTP_TIMEOUT_MS);

  String payload = "{";
  payload += "\"commandId\":\"" + activeCommandId + "\",";
  payload += "\"boxNo\":\"" + String(BOX_NO) + "\",";
  payload += "\"eventType\":\"" + String(eventType) + "\"";

  if (doorState != nullptr) {
    payload += ",\"doorState\":\"" + String(doorState) + "\"";
  }
  if (lockFeedback != nullptr) {
    payload += ",\"lockFeedback\":\"" + String(lockFeedback) + "\"";
  }
  if (detail != nullptr) {
    payload += ",\"detail\":\"" + String(detail) + "\"";
  }

  payload += "}";

  Serial.printf("[callback post] event=%s commandId=%s millis=%lu\n", eventType, activeCommandId.c_str(), millis());
  int httpCode = http.POST(payload);
  String response = http.getString();

  Serial.printf("[callback response] event=%s code=%d body=%s\n", eventType, httpCode, response.c_str());

  http.end();
  return httpCode > 0 && httpCode < 400;
}

bool sendActiveEvent(const char* logLabel, const char* eventType, const char* detail, const char* doorState = nullptr, const char* lockFeedback = nullptr) {
  bool ok = postLockerEvent(eventType, detail, doorState, lockFeedback);
  Serial.printf("[%s %s] commandId=%s\n", logLabel, ok ? "sent" : "fail", activeCommandId.c_str());
  return ok;
}

bool uploadSnapshotJpeg(const String& commandId, const String& sceneValue, const String& taskId, const String& packageId) {
  if (commandId.length() == 0) {
    Serial.printf("[snapshot upload fail] commandId=%s reason=command_missing\n", commandId.c_str());
    return false;
  }

  if (!cameraReady) {
    Serial.printf("[snapshot upload fail] commandId=%s reason=camera_not_ready\n", commandId.c_str());
    return false;
  }

  if (!ensureWiFiReady()) {
    Serial.printf("[snapshot upload fail] commandId=%s reason=wifi_not_ready\n", commandId.c_str());
    return false;
  }

  String captureId = nextSnapshotCaptureId(commandId);
  snapshotUploadStartedMs = millis();
  Serial.printf("[snapshot capture start] captureId=%s commandId=%s taskId=%s scene=%s millis=%lu\n",
    captureId.c_str(),
    commandId.c_str(),
    taskId.c_str(),
    sceneValue.c_str(),
    snapshotUploadStartedMs);

  for (int discardIndex = 1; discardIndex <= 2; discardIndex++) {
    camera_fb_t* discardFb = esp_camera_fb_get();
    if (!discardFb) {
      Serial.printf("[snapshot upload fail] captureId=%s commandId=%s taskId=%s reason=discard_frame_%d_failed\n",
        captureId.c_str(),
        commandId.c_str(),
        taskId.c_str(),
        discardIndex);
      return false;
    }
    Serial.printf("[snapshot discard_frame_%d] captureId=%s commandId=%s taskId=%s millis=%lu size=%u\n",
      discardIndex,
      captureId.c_str(),
      commandId.c_str(),
      taskId.c_str(),
      millis(),
      static_cast<unsigned int>(discardFb->len));
    esp_camera_fb_return(discardFb);
    delay(SNAPSHOT_DISCARD_FRAME_DELAY_MS);
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.printf("[snapshot upload fail] captureId=%s commandId=%s taskId=%s reason=final_capture_failed\n",
      captureId.c_str(),
      commandId.c_str(),
      taskId.c_str());
    return false;
  }

  Serial.printf("[snapshot final_capture_done] captureId=%s commandId=%s taskId=%s millis=%lu fileSize=%u\n",
    captureId.c_str(),
    commandId.c_str(),
    taskId.c_str(),
    millis(),
    static_cast<unsigned int>(fb->len));

  String url = buildUrl("/api/integration/locker/upload_snapshot");
  url += "?commandId=" + commandId;
  url += "&boxNo=" + String(BOX_NO);
  url += "&scene=" + sceneValue;
  url += "&kind=snapshot";
  if (taskId.length() > 0) {
    url += "&taskId=" + taskId;
  }
  if (packageId.length() > 0) {
    url += "&packageId=" + packageId;
  }

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  http.setTimeout(UPLOAD_HTTP_TIMEOUT_MS);

  String timestamp = currentTimestamp();
  Serial.printf("[snapshot upload start] ts=%s captureId=%s millis=%lu commandId=%s taskId=%s scene=%s size=%u\n",
    timestamp.c_str(),
    captureId.c_str(),
    millis(),
    commandId.c_str(),
    taskId.c_str(),
    sceneValue.c_str(),
    static_cast<unsigned int>(fb->len));

  int httpCode = http.POST(fb->buf, fb->len);
  String response = http.getString();

  http.end();
  esp_camera_fb_return(fb);

  bool ok = httpCode > 0 && httpCode < 400;
  Serial.printf("[snapshot upload %s] captureId=%s commandId=%s taskId=%s code=%d body=%s durationMs=%lu\n",
    ok ? "success" : "fail",
    captureId.c_str(),
    commandId.c_str(),
    taskId.c_str(),
    httpCode,
    response.c_str(),
    millis() - snapshotUploadStartedMs);
  return ok;
}

void clearActiveCommand(const char* reason) {
  String finishedCommandId = activeCommandId;

  forceRelayIdle("command_clear");
  activeCommandId = "";
  activeOperatorType = "";
  activeScene = "";
  activeTaskId = "";
  activePackageId = "";
  commandActive = false;
  relayPulsed = false;
  lockOpenedSent = false;
  doorOpenedSent = false;
  doorClosedSent = false;
  relayPulseStartedMs = 0;
  lastLockOpenedAttemptMs = 0;
  lastDoorOpenedAttemptMs = 0;
  lastDoorClosedAttemptMs = 0;
  doorOpenedAtMs = 0;
  resetDoorDebounceState();
  markFastPollWindow("command_cleared");
  scheduleNextPollAfter(0, "command_cleared_ready");

  if (finishedCommandId.length() > 0) {
    Serial.printf("[active command cleared] commandId=%s reason=%s\n", finishedCommandId.c_str(), reason ? reason : "-");
  } else {
    Serial.printf("[active command cleared] reason=%s\n", reason ? reason : "-");
  }
}

void startCommand(const String& commandId, const String& operatorType, const String& sceneValue, const String& taskId, const String& packageId) {
  activeCommandId = commandId;
  activeOperatorType = operatorType;
  activeScene = deriveScene(operatorType, sceneValue);
  activeTaskId = taskId;
  activePackageId = packageId;
  commandActive = true;
  relayPulsed = false;
  relayPulseActive = false;
  lockOpenedSent = false;
  doorOpenedSent = false;
  doorClosedSent = false;
  relayPulseStartedMs = 0;
  lastLockOpenedAttemptMs = 0;
  lastDoorOpenedAttemptMs = 0;
  lastDoorClosedAttemptMs = 0;
  doorOpenedAtMs = 0;
  resetDoorDebounceState();

  Serial.printf("[command accepted] commandId=%s scene=%s operatorType=%s taskId=%s packageId=%s\n",
    activeCommandId.c_str(),
    activeScene.c_str(),
    activeOperatorType.c_str(),
    activeTaskId.c_str(),
    activePackageId.c_str());
}

bool pollNextCommand() {
  unsigned long nowMs = millis();
  unsigned long intervalMs = currentPollIntervalMs();
  unsigned long gapMs = lastActualPollStartMs == 0 ? 0 : nowMs - lastActualPollStartMs;
  String timestamp = currentTimestamp();

  clearPollSkipReason();
  Serial.printf("[poll start] ts=%s millis=%lu gapMs=%lu intervalMs=%lu boxNo=%s deviceId=%s activeCommandId=%s wifi=%s\n",
    timestamp.c_str(),
    nowMs,
    gapMs,
    intervalMs,
    BOX_NO,
    deviceId.c_str(),
    activeCommandId.length() > 0 ? activeCommandId.c_str() : "-",
    wifiStatusText(WiFi.status()));

  if (gapMs >= 10000) {
    Serial.printf("[poll gap alert] level=gt10s gapMs=%lu\n", gapMs);
  } else if (gapMs >= 5000) {
    Serial.printf("[poll gap alert] level=gt5s gapMs=%lu\n", gapMs);
  } else if (gapMs >= 3000) {
    Serial.printf("[poll gap alert] level=gt3s gapMs=%lu\n", gapMs);
  }

  lastActualPollStartMs = nowMs;

  HTTPClient http;
  String url = buildUrl("/api/integration/locker/boxes/" + String(BOX_NO) + "/next_command?deviceId=" + deviceId);
  http.begin(url);
  http.setTimeout(POLL_HTTP_TIMEOUT_MS);

  int httpCode = http.GET();
  String response = http.getString();
  http.end();

  if (httpCode <= 0 || httpCode >= 400) {
    Serial.printf("[poll fail] code=%d\n", httpCode);
    lastPollMs = nowMs;
    scheduleNextPollAfter(POLL_FAIL_RETRY_MS, "poll_http_retry");
    return true;
  }

  String commandRaw = extractJsonRawValue(response, "command");
  String normalizedCommandRaw = toLowerTrimmed(commandRaw);

  if (commandRaw.length() == 0) {
    Serial.println("[poll result] malformed response missing command field");
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "malformed_response_retry");
    return true;
  }

  if (normalizedCommandRaw == "null") {
    Serial.println("[poll result] no command");
    lastPollMs = nowMs;
    scheduleNextPollAfter(IDLE_POLL_INTERVAL_MS, "idle_poll_interval");
    return true;
  }

  String commandJson = commandRaw;
  if (commandJson.length() == 0 || normalizedCommandRaw == "{}" || commandJson.charAt(0) != '{') {
    Serial.printf("[poll result] empty response rawCommand=%s\n", commandRaw.c_str());
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "malformed_response_retry");
    return true;
  }

  String commandId = extractJsonValue(commandJson, "commandId");
  String commandBoxNo = extractJsonValue(commandJson, "boxNo");
  String operatorType = extractJsonValue(commandJson, "operatorType");
  String sceneValue = extractJsonValue(commandJson, "commandScene");
  String taskId = extractJsonValue(commandJson, "taskId");
  String packageId = extractJsonValue(commandJson, "packageId");
  String status = extractJsonValue(commandJson, "status");
  bool deviceMayExecute = extractJsonBoolValue(commandJson, "deviceMayExecute", false);
  bool pendingHardware = extractJsonBoolValue(commandJson, "pendingHardware", false);
  bool completed = extractJsonBoolValue(commandJson, "completed", false);
  bool hasAlert = extractJsonBoolValue(commandJson, "hasAlert", false);

  commandId.trim();
  commandBoxNo.trim();
  operatorType.trim();
  sceneValue.trim();
  taskId.trim();
  packageId.trim();
  status = toLowerTrimmed(status);

  bool emptyShellResponse = (
    commandId.length() == 0 &&
    status.length() == 0 &&
    !deviceMayExecute &&
    !pendingHardware &&
    !completed &&
    !hasAlert
  );

  if (emptyShellResponse) {
    Serial.printf("[poll result] empty response rawCommand=%s\n", commandJson.c_str());
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "malformed_response_retry");
    return true;
  }

  if (commandId.length() == 0) {
    Serial.println("[command rejected] reason=empty_command_id");
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "malformed_response_retry");
    return true;
  }

  if (commandBoxNo != String(BOX_NO)) {
    Serial.printf("[command rejected] reason=box_mismatch responseBox=%s localBox=%s\n", commandBoxNo.c_str(), BOX_NO);
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "rejected_command_retry");
    return true;
  }

  if (!deviceMayExecute) {
    Serial.println("[command rejected] reason=device_may_execute_false");
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "rejected_command_retry");
    return true;
  }

  if (!pendingHardware || completed || hasAlert || !isExecutableCommandStatus(status)) {
    Serial.println("[command rejected] reason=not_executable");
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "rejected_command_retry");
    return true;
  }

  if (commandActive && commandId == activeCommandId) {
    Serial.printf("[command rejected] reason=already_active commandId=%s\n", commandId.c_str());
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "rejected_command_retry");
    return true;
  }

  if (commandId == lastExecutedCommandId) {
    Serial.printf("[command rejected] reason=already_executed commandId=%s\n", commandId.c_str());
    lastPollMs = nowMs;
    scheduleNextPollAfter(MALFORMED_POLL_RETRY_MS, "rejected_command_retry");
    return true;
  }

  Serial.printf("[poll result] valid command received commandId=%s boxNo=%s status=%s deviceMayExecute=%d pendingHardware=%d completed=%d hasAlert=%d\n",
    commandId.c_str(),
    commandBoxNo.c_str(),
    status.c_str(),
    deviceMayExecute ? 1 : 0,
    pendingHardware ? 1 : 0,
    completed ? 1 : 0,
    hasAlert ? 1 : 0);
  lastPollMs = nowMs;
  scheduleNextPollAfter(COMMAND_CANDIDATE_COOLDOWN_MS, "command_candidate_cooldown");

  startCommand(commandId, operatorType, sceneValue, taskId, packageId);
  return true;
}

bool maybePollNextCommand() {
  if (commandActive) {
    logPollSkip("active_command_running", String("commandId=") + activeCommandId);
    return false;
  }

  if (!ensureWiFiReady()) {
    if (wifiConnectInFlight) {
      logPollSkip("wifi_reconnect_in_progress", String("status=") + wifiStatusText(WiFi.status()));
    } else if (millis() - lastWiFiConnectAttemptMs < WIFI_RETRY_INTERVAL_MS) {
      logPollSkip("wifi_retry_backoff", String("remainingMs=") + String(WIFI_RETRY_INTERVAL_MS - (millis() - lastWiFiConnectAttemptMs)));
    } else {
      logPollSkip("wifi_disconnected", String("status=") + wifiStatusText(WiFi.status()));
    }
    return false;
  }

  long remainingMs = static_cast<long>(nextPollAllowedMs - millis());
  if (remainingMs > 0) {
    logPollSkip(nextPollGateReason.c_str(), String("remainingMs=") + String(remainingMs));
    return false;
  }

  return pollNextCommand();
}

void processActiveCommand() {
  if (!commandActive) return;

  bool doorOpen = readDoorOpenStable();
  unsigned long nowMs = millis();

  if (!relayPulsed) {
    beginRelayPulse();
  }

  if (!lockOpenedSent && nowMs - lastLockOpenedAttemptMs >= RETRY_INTERVAL_MS) {
    lastLockOpenedAttemptMs = nowMs;
    lockOpenedSent = sendActiveEvent(
      "lock_opened",
      "lock_opened",
      "ESP32-CAM reported unlock feedback",
      nullptr,
      "unlocked"
    );
    return;
  }

  if (!lockOpenedSent) {
    return;
  }

  if (!doorOpenedSent && doorOpen && nowMs - lastDoorOpenedAttemptMs >= RETRY_INTERVAL_MS) {
    lastDoorOpenedAttemptMs = nowMs;
    doorOpenedSent = sendActiveEvent(
      "door_opened",
      "door_opened",
      "Door magnet detected opened",
      "open",
      nullptr
    );
    if (doorOpenedSent) {
      doorOpenedAtMs = nowMs;
    }
    return;
  }

  if (!doorOpenedSent) {
    return;
  }

  if (!doorClosedSent && !doorOpen && nowMs - doorOpenedAtMs >= DOOR_OPEN_HOLD_MS && nowMs - lastDoorClosedAttemptMs >= RETRY_INTERVAL_MS) {
    lastDoorClosedAttemptMs = nowMs;
    doorClosedSent = sendActiveEvent(
      "door_closed",
      "door_closed",
      "Door magnet detected closed",
      "closed",
      nullptr
    );
    if (doorClosedSent) {
      bool uploaded = uploadSnapshotJpeg(activeCommandId, activeScene, activeTaskId, activePackageId);
      Serial.printf("[snapshot final] commandId=%s result=%s\n",
        activeCommandId.c_str(),
        uploaded ? "uploaded" : "failed");
      clearActiveCommand(uploaded ? "door_closed_snapshot_uploaded" : "door_closed_snapshot_failed");
    }
  }
}

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);

  Serial.begin(115200);
  delay(50);
  Serial.println();
  Serial.println("[boot] relay forced idle before init");

  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.mode(WIFI_STA);

  pinMode(DOOR_SENSOR_PIN, INPUT_PULLUP);
  resetDoorDebounceState();

  deviceId = getDeviceId();
  Serial.printf("[boot] deviceId=%s boxNo=%s\n", deviceId.c_str(), BOX_NO);

  loadLastExecutedCommandId();
  markFastPollWindow("boot");
  beginWiFiConnect("boot", true);
  scheduleNextPollAfter(0, "boot_ready");
  cameraReady = initCamera();
  if (!cameraReady) {
    Serial.println("[camera] snapshot capture unavailable until camera init succeeds");
  }
  Serial.println("[boot] device standby and waiting for locker commands");
}

void loop() {
  serviceRelayPulse();
  serviceWiFiConnection();

  if (commandActive) {
    processActiveCommand();
  } else {
    maybePollNextCommand();
  }

  delay(5);
}
