# Setup beyond the software demo

## Configuration

The backend loads `.env` from the repository root. Existing environment variables take precedence. The public defaults use mock identities, mock locker control and loopback networking. Generated databases, artifacts, local `.env` files and firmware `config.local.h` files are ignored by Git.

| Variable | Purpose |
| --- | --- |
| `USE_MOCK` | Enable the built-in demo identities |
| `FLASK_SECRET_KEY` | Stable local session secret; otherwise generated at startup |
| `DATABASE_URL` | Optional SQLAlchemy database URI; default is `backend/database.db` |
| `LOCKER_CONTROL_MODE` | `mock` for the walkthrough; `hardware` for ESP32 polling |
| `LOCKER_MOCK_AUTO_COMPLETE` | Simulate the locker event cycle in mock mode |
| `WX_APP_ID`, `WX_APP_SECRET` | Your own WeChat credentials for real WeChat login |
| `FLASK_HOST` | Default `127.0.0.1`; a LAN bind is needed only for a controlled device test |
| `AI_YOLO_ROOT` | Absolute path to your compatible external YOLOv5 checkout |
| `AI_LOCAL_WEIGHTS` | Absolute path to your package-detection weights |
| `AI_PYTHON_BIN` | Interpreter for the separate vision environment |
| `ARTIFACTS_DIR` | Optional directory for generated snapshots and traces |

## WeChat Mini Program

1. Import `frontend/` in WeChat DevTools. `project.config.json` contains the placeholder `touristappid`; use your own test AppID if the tool requires one.
2. Start the backend and configure `frontend/utils/api-config.js` for your backend host. The default is `http://127.0.0.1:5000` for local development.
3. Real-device access requires a reachable LAN address; the device's `127.0.0.1` refers to itself. Configure the appropriate WeChat development environment and request-domain settings for your account.
4. Use the demo identities when the backend has `USE_MOCK=true`. Set your own WeChat credentials privately for the real login path.

The Mini Program was syntax-checked for this publication snapshot; its UI was not run in WeChat DevTools during preparation. Do not treat browser screenshots as Mini Program screenshots.

## ESP32-CAM

Install the board support using [Espressif's Arduino-ESP32 instructions](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html). Open one sketch directory at a time.

- `locker_cam_poller/`: polling, relay control, door-state callbacks and snapshot uploads.
- `camera_dataset_collector/`: separate camera-data collection utility.

Copy the sketch's `config.example.h` to `config.local.h`, then set your Wi-Fi SSID/password and backend LAN URL. The local header is intentionally excluded from Git.

The locker sketch uses GPIO 14 for the relay and GPIO 13 for the door sensor, with a one-second relay pulse and an 80 ms debounce interval. It contains an explicit ESP32-CAM camera pin map; verify it against your actual module and wiring. The two original `.ino` copies have been reduced to one sketch to avoid duplicate definitions during Arduino compilation.

Use `LOCKER_CONTROL_MODE=hardware` for the device path. Device callbacks, uploads and command polling are prototype endpoints; run this only on a controlled local network. See [limitations](limitations.md).

## Real YOLOv5 inference

The public repository includes the adapter, not the model or its runtime. Obtain a compatible [YOLOv5 checkout](https://github.com/ultralytics/yolov5) in an isolated Python environment and install its documented dependencies. Set `AI_YOLO_ROOT`, `AI_LOCAL_WEIGHTS` and `AI_PYTHON_BIN` to that environment. Consult the source and model licence applicable to the version you use.

An example invocation from the repository root, after replacing every placeholder path:

```bash
python ai/inference/detect_snapshot.py --image /absolute/path/snapshot.jpg --weights /absolute/path/best.pt --yolo-root /absolute/path/yolov5
```

The adapter invokes `detect.py` with text-label and confidence output, then returns JSON. Detection files are stored in a runtime directory beside the external YOLOv5 directory. The weights must be suitable for the intended package class; the wrapper currently treats any returned detection as a candidate item. No accuracy threshold beyond the configurable confidence filter, per-class evaluation, or deployment benchmark is supplied here.
