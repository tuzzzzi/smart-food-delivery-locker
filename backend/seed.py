from app import create_app
from extensions import db
from models import Box

def seed_boxes():
    box_nos = [f"A{str(i).zfill(2)}" for i in range(1, 11)] + [f"B{str(i).zfill(2)}" for i in range(1, 11)]

    created = 0
    for no in box_nos:
        if Box.query.get(no):
            continue
        db.session.add(Box(box_no=no, status="empty", task_id=None))
        created += 1

    db.session.commit()
    print(f"✅ seeded boxes: {created}/{len(box_nos)}")

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_boxes()
