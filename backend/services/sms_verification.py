import random
import re
import time


_sms_cache = {}


def send_sms_code(openid: str, phone: str):
    openid = (openid or "").strip()
    phone = (phone or "").strip()

    if not openid:
        return False, "Missing token", 401, {}
    if not phone or not re.fullmatch(r"\d{11}", phone):
        return False, "手机号必须为 11 位数字", 400, {}

    code = "".join(str(random.randint(0, 9)) for _ in range(6))
    _sms_cache[openid] = {
        "code": code,
        "phone": phone,
        "expire": int(time.time()) + 300,
    }

    print(f"[SMS MOCK] openid={openid} phone={phone} code={code} expire=300s")
    return True, "验证码已发送（模拟）", 200, {"expiresInSec": 300}


def consume_sms_code(openid: str, phone: str, code: str):
    openid = (openid or "").strip()
    phone = (phone or "").strip()
    code = (code or "").strip()

    if not openid:
        return False, "Missing token", 401
    if not phone or not code:
        return False, "缺少手机号或验证码", 400
    if not re.fullmatch(r"\d{11}", phone):
        return False, "手机号必须为 11 位数字", 400
    if not re.fullmatch(r"\d{6}", code):
        return False, "请输入6位验证码", 400

    item = _sms_cache.get(openid)
    if not item:
        return False, "请先发送验证码", 400
    if phone != (item.get("phone") or ""):
        return False, "手机号与发送验证码时不一致", 400
    if int(time.time()) > int(item.get("expire") or 0):
        _sms_cache.pop(openid, None)
        return False, "验证码已过期", 400
    if code != (item.get("code") or ""):
        return False, "验证码错误", 400

    _sms_cache.pop(openid, None)
    return True, "验证码通过", 200
