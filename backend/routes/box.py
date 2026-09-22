from flask import Blueprint, jsonify

from models import Box
from services.locker_gateway import get_box_locker_status_map

box_bp = Blueprint("box", __name__)


@box_bp.get("/api/boxes")
def get_boxes():
    boxes = Box.query.order_by(Box.box_no.asc()).all()
    hardware_map = get_box_locker_status_map([box.box_no for box in boxes])

    return jsonify({
        "status": "success",
        "boxes": [
            {
                **box.to_dict(),
                "hardware": hardware_map.get(box.box_no),
            }
            for box in boxes
        ],
    })
