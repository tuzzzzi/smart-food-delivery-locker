# API reading map

These are selected routes from the application, not a promise of a production API contract. Demo tokens are the synthetic OpenIDs listed in `backend/services/mock_identities.py` and can be sent as `Authorization: Bearer <openid>`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/mock/identities` | List available mock identities when enabled |
| POST | `/api/login` | Mock or WeChat login path |
| POST | `/api/platform/mock/push_order` | Create/update a demo platform order |
| POST | `/api/admin/platform/orders/<id>/dispatch` | Dispatch through the admin-guarded route |
| GET | `/api/courier/active_task` | Current courier's active task |
| POST | `/api/courier/open_locker_for_task` | Begin courier deposit access |
| POST | `/api/ai/verify_box` | Verify a task in `waiting_ai` |
| GET | `/api/user/home` | Recipient's package overview |
| POST | `/api/user/find_by_code` | Find an owned package by pickup code |
| POST | `/api/user/open_door_by_package` | Request pickup for an owned package |
| GET | `/api/integration/locker/boxes/<box>/next_command` | Device command polling |
| POST | `/api/integration/locker/callback` | Device event callback |
| POST | `/api/integration/locker/upload_snapshot` | Upload a JPEG tied to a command |
| POST | `/api/integration/locker/refresh_timeouts` | Refresh device timeout state |

For an executable request sequence, read `backend/demo.py`. It creates a synthetic recipient order, dispatches it to `mock_courier_a`, requests deposit, supplies an explicitly simulated vision result, and collects the package as `mock_user_a`.

Several integration and platform routes do not have production authentication. The admin-prefixed dispatch route and user/courier guards do not secure the entire API surface. See [limitations](limitations.md).
