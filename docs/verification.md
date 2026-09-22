# Verification record

Publication preparation: 22 September 2026.

## Executed locally

- Installed backend dependencies into a new Python 3.12 virtual environment on Windows.
- Ran five isolated integration tests: admin access, courier task ownership, missing snapshot handling, delivery/pickup/repeat pickup, and recipient package ownership.
- Ran `demo.py` through order completion and `demo.py --leave-ready` to retain a pending package.
- Opened the running admin console, signed in with the built-in synthetic Admin identity and inspected the generated dashboard.
- Parsed 38 Python files and 17 Mini Program JSON files; ran `node --check` on all 19 JavaScript files.
- Checked relative Markdown file links and scanned the publication files for the sensitive literals removed from the original configuration.

The tests use a temporary SQLite database and temporary artifact storage. Successful vision is explicitly stubbed in the walkthrough/tests; the missing-snapshot test exercises the real inference gateway. Mock locker mode supplies simulated door events.

## Not executed here

WeChat DevTools UI testing, firmware compilation/flashing, physical lock/camera verification and real YOLOv5 inference are not included in these results.

## Executed on GitHub

The initial published source commit, `8871ed51c079920944c7dea7c69d3d484b5d6691`, passed all three jobs on GitHub-hosted Ubuntu runners: backend on Python 3.11, backend on Python 3.12, and JavaScript syntax validation on Node.js 22.

[View the successful workflow run](https://github.com/tuzzzzi/smart-food-delivery-locker/actions/runs/35702312617). Each backend job ran all five integration tests and Python compilation checks. All 140 published file hashes were also compared with the audited local Git tree and matched.
