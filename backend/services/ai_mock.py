import time, random, string

# ====== （可选）AI检测占位：先mock，后面接YOLO ======
ai_detect_count = {}

def ai_detect_package_in_box(box_no: str) -> bool:
    """
    第一次失败，第二次成功
    """
    count = ai_detect_count.get(box_no, 0)
    if count == 0:
        ai_detect_count[box_no] = 1
        return False
    return True
#return true # 临时始终返回True，可以在此改为随机返回 True/False 模拟失败

def gen_pickup_code():
    return "".join(random.choices(string.digits, k=6))
