# Prototype scope and next steps

This is a local academic prototype. Publishing the source is separate from deploying a publicly reachable locker service.

## What is implemented

Mini Program views, Flask APIs, an admin console, SQLite persistence, dispatch and pickup flows, locker command/event tracking, ESP32 firmware, snapshot uploads and a host-side YOLOv5 adapter are present in source.

## What is simulated or reserved

- Built-in mock identities are for demonstrations. Current request tokens use OpenIDs directly, rather than independently issued expiring credentials.
- The software walkthrough simulates the door cycle and patches a successful detector result. It does not execute YOLOv5.
- Platform callback delivery is a reserved integration. It does not send a real external food-delivery-platform callback.
- SMS verification and notification-related code should not be interpreted as a verified commercial SMS integration.
- Model weights, raw datasets, private records and historical device artifacts are not redistributed.

## Before any real deployment

Add proper session/token validation, authenticated device enrollment and callbacks, replay protection, consistent route authorization, CSRF protection for admin mutations, upload validation/limits, and transport protection. Replace the development server and assess concurrent database updates and atomic locker allocation. Verify electrical wiring, firmware compilation, device timing and failure recovery on actual hardware.

Some legacy SQLAlchemy `Query.get()` calls, naive UTC timestamps and cyclic ORM relationships remain. Local tests currently emit deprecation/schema warnings. They are disclosed rather than described as a completed modernisation.

## Evidence limits

The supplied screenshot demonstrates the running admin interface with synthetic records. This publication preparation did not establish a physical deployment, WeChat UI compatibility, detection precision/recall, throughput, or performance across multiple simultaneous lockers. Those claims require their own measurements.
