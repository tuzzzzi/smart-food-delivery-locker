#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <time.h>
#include "esp_camera.h"

#include "config.local.h"
const char* DATASET_UPLOAD_PATH = "/api/dataset/upload_raw";

const unsigned long WIFI_CONNECT_TIMEOUT_MS = 12000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 5000;
const unsigned long STATUS_LOG_INTERVAL_MS = 5000;
const unsigned long UPLOAD_HTTP_TIMEOUT_MS = 12000;
const unsigned long DISCARD_FRAME_DELAY_MS = 80;

const framesize_t CAMERA_CAPTURE_FRAME_SIZE = FRAMESIZE_SXGA;
const char* CAMERA_CAPTURE_FRAME_LABEL = "SXGA";
const int CAMERA_CAPTURE_WIDTH = 1280;
const int CAMERA_CAPTURE_HEIGHT = 1024;

// Camera pin map kept aligned with the production ESP32-CAM profile.
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

WebServer server(80);

String deviceId = "";
String lastErrorMessage = "";
String lastUploadPath = "";
String lastUploadResponse = "";
String lastCaptureReason = "idle";
String lastCaptureId = "";

bool cameraReady = false;
bool autoCaptureEnabled = false;
bool captureInProgress = false;
bool lastUploadOk = false;
bool wifiConnectInFlight = false;

unsigned long lastUploadMs = 0;
unsigned long lastFileSize = 0;
unsigned long captureCount = 0;
unsigned long uploadCount = 0;
unsigned long wifiConnectStartedMs = 0;
unsigned long lastWiFiConnectAttemptMs = 0;
unsigned long lastStatusLogMs = 0;
unsigned long captureSerial = 0;

wl_status_t lastWiFiStatus = WL_IDLE_STATUS;

String buildUrl(const String& path) {
  return String(BASE_URL) + path;
}

String getDeviceId() {
  uint64_t chipId = ESP.getEfuseMac();
  char buf[32];
  snprintf(buf, sizeof(buf), "esp32-cam-%04X%08X", (uint16_t)(chipId >> 32), (uint32_t)chipId);
  return String(buf);
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

String nextCaptureId() {
  captureSerial++;
  return deviceId + "-" + String(millis()) + "-" + String(captureSerial);
}

void beginWiFiConnect(const char* reason, bool force = false) {
  unsigned long nowMs = millis();
  if (!force && nowMs - lastWiFiConnectAttemptMs < WIFI_RETRY_INTERVAL_MS) {
    return;
  }

  Serial.printf("wifi_connect_start reason=%s ssid=%s\n", reason ? reason : "-", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  wifiConnectInFlight = true;
  wifiConnectStartedMs = nowMs;
  lastWiFiConnectAttemptMs = nowMs;
}

void serviceWiFiConnection() {
  wl_status_t status = WiFi.status();
  unsigned long nowMs = millis();
  wl_status_t previousStatus = lastWiFiStatus;

  if (status != lastWiFiStatus) {
    Serial.printf("wifi_status from=%s to=%s\n", wifiStatusText(lastWiFiStatus), wifiStatusText(status));
    lastWiFiStatus = status;
  }

  if (status == WL_CONNECTED) {
    if (wifiConnectInFlight || previousStatus != WL_CONNECTED) {
      Serial.printf("wifi_connected ip=%s connect_ms=%lu\n", WiFi.localIP().toString().c_str(), wifiConnectStartedMs == 0 ? 0 : nowMs - wifiConnectStartedMs);
      Serial.printf("ip=%s\n", WiFi.localIP().toString().c_str());
      wifiConnectInFlight = false;
      wifiConnectStartedMs = 0;
    }
    return;
  }

  if (wifiConnectInFlight && nowMs - wifiConnectStartedMs >= WIFI_CONNECT_TIMEOUT_MS) {
    Serial.printf("wifi_connect_timeout waited_ms=%lu status=%s\n", nowMs - wifiConnectStartedMs, wifiStatusText(status));
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
  config.frame_size = CAMERA_CAPTURE_FRAME_SIZE;
  config.jpeg_quality = 18;
  config.fb_count = 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.print("camera_init_failed code=0x");
    Serial.println(err, HEX);
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor) {
    sensor->set_framesize(sensor, CAMERA_CAPTURE_FRAME_SIZE);
    sensor->set_brightness(sensor, 1);
    sensor->set_saturation(sensor, -1);
  }

  Serial.printf("camera_ready frame=%s resolution=%dx%d jpeg_quality=18 fb_count=1\n",
    CAMERA_CAPTURE_FRAME_LABEL,
    CAMERA_CAPTURE_WIDTH,
    CAMERA_CAPTURE_HEIGHT);
  return true;
}

String buildStatusJson() {
  String json = "{";
  json += "\"status\":\"success\"";
  json += ",\"deviceId\":\"" + deviceId + "\"";
  json += ",\"cameraReady\":" + String(cameraReady ? "true" : "false");
  json += ",\"wifiStatus\":\"" + String(wifiStatusText(WiFi.status())) + "\"";
  json += ",\"ip\":\"" + WiFi.localIP().toString() + "\"";
  json += ",\"autoCaptureEnabled\":" + String(autoCaptureEnabled ? "true" : "false");
  json += ",\"captureInProgress\":" + String(captureInProgress ? "true" : "false");
  json += ",\"captureCount\":" + String(captureCount);
  json += ",\"uploadCount\":" + String(uploadCount);
  json += ",\"lastFileSize\":" + String(lastFileSize);
  json += ",\"lastUploadMs\":" + String(lastUploadMs);
  json += ",\"lastUploadOk\":" + String(lastUploadOk ? "true" : "false");
  json += ",\"frameSize\":\"" + String(CAMERA_CAPTURE_FRAME_LABEL) + "\"";
  json += ",\"frameWidth\":" + String(CAMERA_CAPTURE_WIDTH);
  json += ",\"frameHeight\":" + String(CAMERA_CAPTURE_HEIGHT);
  json += ",\"lastCaptureId\":\"" + lastCaptureId + "\"";
  json += ",\"lastCaptureReason\":\"" + lastCaptureReason + "\"";
  json += ",\"lastUploadPath\":\"" + lastUploadPath + "\"";
  json += ",\"lastErrorMessage\":\"" + lastErrorMessage + "\"";
  json += "}";
  return json;
}

String buildIndexHtml() {
  String html = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>ESP32 Dataset Collector</title>";
  html += "<style>body{font-family:Arial,sans-serif;background:#f3f5f7;color:#1f2937;padding:20px;}main{max-width:720px;margin:0 auto;background:#fff;padding:20px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.08);}button{margin:6px 8px 6px 0;padding:10px 14px;border:0;border-radius:8px;background:#0f766e;color:#fff;font-weight:700;cursor:pointer;}code,pre{background:#f8fafc;padding:10px;border-radius:8px;display:block;overflow:auto;}</style>";
  html += "</head><body><main><h1>ESP32-CAM Dataset Collector</h1>";
  html += "<p>Device: <b>" + deviceId + "</b></p>";
  html += "<p>Upload: <code>" + buildUrl(DATASET_UPLOAD_PATH) + "</code></p>";
  html += "<div><button onclick='captureNow()'>Capture One</button></div>";
  html += "<pre id='status'>Loading...</pre>";
  html += "<script>";
  html += "async function refreshStatus(){const r=await fetch('/status?t='+Date.now(),{cache:'no-store'});document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2);}";
  html += "async function captureNow(){const b=document.querySelector('button');b.disabled=true;try{await fetch('/capture?t='+Date.now(),{cache:'no-store'});}finally{setTimeout(async()=>{await refreshStatus();b.disabled=false;},300);}}";
  html += "refreshStatus();setInterval(refreshStatus,2000);";
  html += "</script></main></body></html>";
  return html;
}

bool uploadFrame(camera_fb_t* fb, const char* reason, const String& captureId, String& savedPathOut) {
  if (!fb) {
    lastErrorMessage = "capture buffer missing";
    return false;
  }
  if (!ensureWiFiReady()) {
    lastErrorMessage = "wifi not ready";
    return false;
  }

  HTTPClient http;
  String url = buildUrl(DATASET_UPLOAD_PATH);
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Device-Id", deviceId);
  http.addHeader("X-Capture-Mode", reason ? reason : "manual");
  http.addHeader("X-Capture-Id", captureId);
  http.setTimeout(UPLOAD_HTTP_TIMEOUT_MS);

  Serial.printf("upload_start captureId=%s reason=%s size=%u millis=%lu\n",
    captureId.c_str(),
    reason ? reason : "-",
    static_cast<unsigned int>(fb->len),
    millis());
  unsigned long uploadStartedAt = millis();
  int httpCode = http.POST(fb->buf, fb->len);
  String response = http.getString();
  unsigned long uploadDuration = millis() - uploadStartedAt;
  http.end();

  lastUploadMs = uploadDuration;
  lastUploadResponse = response;
  savedPathOut = extractJsonValue(response, "savedPath");
  if (httpCode > 0 && httpCode < 400) {
    lastUploadPath = savedPathOut;
    uploadCount++;
    lastUploadOk = true;
    lastErrorMessage = "";
    Serial.printf("upload_done captureId=%s status=success http_code=%d savedPath=%s body=%s\n",
      captureId.c_str(),
      httpCode,
      savedPathOut.c_str(),
      response.c_str());
  } else {
    lastUploadOk = false;
    lastErrorMessage = "upload failed";
    Serial.printf("upload_failed captureId=%s http_code=%d body=%s\n",
      captureId.c_str(),
      httpCode,
      response.c_str());
  }
  Serial.printf("upload_ms=%lu\n", uploadDuration);
  return httpCode > 0 && httpCode < 400;
}

bool performCaptureAndUpload(const char* reason, String& savedPathOut) {
  if (captureInProgress) {
    lastErrorMessage = "capture already running";
    return false;
  }
  if (!cameraReady) {
    lastErrorMessage = "camera not ready";
    return false;
  }

  captureInProgress = true;
  lastCaptureId = nextCaptureId();
  lastCaptureReason = reason ? reason : "manual";
  lastUploadOk = false;
  savedPathOut = "";

  unsigned long captureStartedAt = millis();
  Serial.printf("capture_start captureId=%s reason=%s millis=%lu\n",
    lastCaptureId.c_str(),
    lastCaptureReason.c_str(),
    captureStartedAt);

  for (int discardIndex = 1; discardIndex <= 2; discardIndex++) {
    camera_fb_t* discardFb = esp_camera_fb_get();
    if (!discardFb) {
      captureInProgress = false;
      lastErrorMessage = "discard frame failed";
      Serial.printf("capture_done captureId=%s status=fail stage=discard_frame_%d\n", lastCaptureId.c_str(), discardIndex);
      return false;
    }
    Serial.printf("discard_frame_%d captureId=%s millis=%lu size=%u\n",
      discardIndex,
      lastCaptureId.c_str(),
      millis(),
      static_cast<unsigned int>(discardFb->len));
    esp_camera_fb_return(discardFb);
    delay(DISCARD_FRAME_DELAY_MS);
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    captureInProgress = false;
    lastErrorMessage = "camera capture failed";
    Serial.printf("capture_done captureId=%s status=fail stage=final_capture\n", lastCaptureId.c_str());
    return false;
  }

  lastFileSize = fb->len;
  captureCount++;
  Serial.printf("final_capture_done captureId=%s millis=%lu\n", lastCaptureId.c_str(), millis());
  Serial.printf("file_size=%u\n", static_cast<unsigned int>(fb->len));
  Serial.printf("capture_done captureId=%s status=success\n", lastCaptureId.c_str());

  bool uploaded = uploadFrame(fb, reason, lastCaptureId, savedPathOut);
  esp_camera_fb_return(fb);
  captureInProgress = false;
  return uploaded;
}

void handleIndex() {
  server.sendHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
  server.sendHeader("Pragma", "no-cache");
  server.sendHeader("Expires", "0");
  server.send(200, "text/html", buildIndexHtml());
}

void handleStatus() {
  server.sendHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
  server.sendHeader("Pragma", "no-cache");
  server.sendHeader("Expires", "0");
  server.send(200, "application/json", buildStatusJson());
}

void handleCapture() {
  String savedPath;
  bool ok = performCaptureAndUpload("manual", savedPath);
  int code = ok ? 200 : 500;
  server.sendHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
  server.sendHeader("Pragma", "no-cache");
  server.sendHeader("Expires", "0");
  server.send(code, "application/json",
    String("{\"status\":\"") + (ok ? "success" : "error") +
    "\",\"captureId\":\"" + lastCaptureId +
    "\",\"savedPath\":\"" + savedPath +
    "\",\"fileSize\":" + String(lastFileSize) +
    ",\"uploadMs\":" + String(lastUploadMs) +
    ",\"message\":\"" + (ok ? "captured" : lastErrorMessage) + "\"}");
}

void handleNotFound() {
  server.send(404, "application/json", "{\"status\":\"error\",\"message\":\"Not found\"}");
}

void registerRoutes() {
  server.on("/", HTTP_GET, handleIndex);
  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/status", HTTP_GET, handleStatus);
  server.onNotFound(handleNotFound);
}

void maybeLogStatus() {
  unsigned long nowMs = millis();
  if (nowMs - lastStatusLogMs < STATUS_LOG_INTERVAL_MS) {
    return;
  }
  lastStatusLogMs = nowMs;
  Serial.printf("status wifi=%s ip=%s auto=%d capture_count=%lu upload_count=%lu last_ok=%d\n",
    wifiStatusText(WiFi.status()),
    WiFi.localIP().toString().c_str(),
    autoCaptureEnabled ? 1 : 0,
    captureCount,
    uploadCount,
    lastUploadOk ? 1 : 0);
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println();
  Serial.println("dataset_collector_boot");

  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.mode(WIFI_STA);

  deviceId = getDeviceId();
  Serial.printf("device_id=%s\n", deviceId.c_str());

  beginWiFiConnect("boot", true);
  cameraReady = initCamera();
  registerRoutes();
  server.begin();
  Serial.println("http_server_ready port=80");
}

void loop() {
  serviceWiFiConnection();
  server.handleClient();
  maybeLogStatus();
  delay(5);
}
