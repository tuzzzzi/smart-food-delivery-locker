import time, random

# 你原来用的 10 个箱体示例（A01-A05, B01-B05）
BOXES = [f"A{str(i).zfill(2)}" for i in range(1, 6)] + [f"B{str(i).zfill(2)}" for i in range(1, 6)]

box_status = {b: "empty" for b in BOXES}  # empty/reserved/occupied
tasks = {}
packages = {}

def gen_id(prefix):
    return f"{prefix}-{int(time.time())}-{random.randint(100,999)}"

def assign_empty_box():
    for b, st in box_status.items():
        if st == "empty":
            box_status[b] = "reserved"
            return b
    return None
