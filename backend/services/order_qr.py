import os
import re
import secrets
import string
import time

from flask import current_app

from models import PlatformOrder


QR_REL_DIR = os.path.join("orders", "qrcode")
QR_VERIFY_PREFIX = "QR_VERIFY:"
QR_BOX_SIZE = 12
QR_BORDER = 6
_TOKEN_ALPHABET = string.ascii_letters + string.digits
_CODE_ALPHABET = string.digits


def _safe_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "")
    return cleaned.strip("._") or secrets.token_hex(8)


def generate_qr_token():
    for _ in range(30):
        token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(24))
        if not PlatformOrder.query.filter_by(qr_token=token).first():
            return token
    raise RuntimeError("Unable to allocate unique QR token")


def generate_verify_code():
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def build_verify_path(token):
    return f"{QR_VERIFY_PREFIX}{(token or '').strip()}"


def _artifact_abs_path(rel_path):
    artifacts_dir = current_app.config.get("ARTIFACTS_DIR", "")
    return os.path.join(artifacts_dir, rel_path)


def _is_current_png(order, rel_path):
    token = (getattr(order, "qr_token", "") or "").strip()
    if not rel_path or not rel_path.lower().endswith(".png"):
        return False
    if token and token not in os.path.basename(rel_path):
        return False
    return os.path.exists(_artifact_abs_path(rel_path))


def _needs_image(order):
    rel_path = (getattr(order, "qr_image_path", "") or "").strip()
    if os.path.isabs(rel_path):
        return True
    return not _is_current_png(order, rel_path)


def _write_qr_png(content, abs_path):
    if not content or not content.startswith(QR_VERIFY_PREFIX):
        raise ValueError("QR content must be QR_VERIFY:<token>")

    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_H
    except Exception as exc:
        raise RuntimeError("qrcode[pil] is required to generate stable PNG QR codes") from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    image.save(abs_path, format="PNG")
    return image.size, image.mode


def _remove_old_image(rel_path):
    rel_path = (rel_path or "").strip()
    if not rel_path or os.path.isabs(rel_path):
        return
    abs_path = _artifact_abs_path(rel_path)
    if os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass


def ensure_order_qr(order):
    if not order:
        return False

    changed = False
    if not (order.qr_token or "").strip():
        order.qr_token = generate_qr_token()
        changed = True
    if not (order.verify_code or "").strip():
        order.verify_code = generate_verify_code()
        changed = True

    content = build_verify_path(order.qr_token)
    if _needs_image(order):
        old_path = (order.qr_image_path or "").strip()
        rel_dir = QR_REL_DIR.replace("\\", "/")
        filename = (
            f"{_safe_filename(order.platform_order_id)}-"
            f"{_safe_filename(order.qr_token)}-"
            f"{int(time.time())}.png"
        )
        rel_path = f"{rel_dir}/{filename}"
        abs_dir = _artifact_abs_path(rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        size, mode = _write_qr_png(content, _artifact_abs_path(rel_path))
        _remove_old_image(old_path)
        order.qr_image_path = rel_path
        current_app.logger.info(
            "order_qr.generated order=%s content=%s path=%s size=%s mode=%s",
            order.platform_order_id,
            content,
            rel_path,
            size,
            mode,
        )
        changed = True

    return changed
