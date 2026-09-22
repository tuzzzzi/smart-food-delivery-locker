"""Exercise real application routes with synthetic identities and simulated I/O.

Vision is stubbed only inside this walkthrough. This is not a YOLO benchmark.
Run from backend/: python demo.py [--leave-ready]
"""
import argparse
from uuid import uuid4
from unittest.mock import patch

from app import app
from config import Config
from extensions import db
from models import Box, Package, PlatformOrder
from seed import seed_boxes
from services.mock_identities import ensure_mock_identities, get_mock_identity


def checked(response):
    payload = response.get_json()
    if response.status_code != 200 or not payload or payload.get('status') != 'success':
        raise RuntimeError(f'Unexpected response {response.status_code}: {payload}')
    return payload


def prepare_delivery(client):
    """Create, dispatch and deposit a synthetic order through real HTTP handlers."""
    recipient = get_mock_identity('user_a')
    order = checked(client.post('/api/platform/mock/push_order', json={
        'platformName': 'Portfolio Demo',
        'externalOrderNo': 'DEMO-' + uuid4().hex[:12],
        'receiverPhone': recipient['phone_number'],
        'receiverName': 'Demo Recipient A',
        'receiverAddress': 'Demo collection point',
        'merchantName': 'Demo Kitchen',
    }))['order']
    order_id = order['platformOrderId']
    dispatched = checked(client.post(f'/api/admin/platform/orders/{order_id}/dispatch',
        headers={'Authorization': 'Bearer mock_admin_main'},
        json={'courierOpenid': 'mock_courier_a'}))
    task_id = dispatched['task']['taskId']
    checked(client.post('/api/courier/open_locker_for_task',
        headers={'Authorization': 'Bearer mock_courier_a'}, json={'taskId': task_id}))
    return order_id, task_id


def simulate_vision(client, task_id):
    result = {'ok': True, 'detectorStatus': 'ok', 'mode': 'mock',
              'simulated': True, 'detail': 'Synthetic detection for the software walkthrough; no model executed.'}
    with patch('routes.ai.request_box_detection', return_value=result):
        return checked(client.post('/api/ai/verify_box', json={'taskId': task_id}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--leave-ready', action='store_true', help='Leave a demo package waiting for pickup in the admin UI.')
    args = parser.parse_args()
    if not Config.USE_MOCK or app.config['LOCKER_CONTROL_MODE'] != 'mock':
        raise SystemExit('This walkthrough requires USE_MOCK=true and LOCKER_CONTROL_MODE=mock.')
    with app.app_context():
        seed_boxes()
        ensure_mock_identities()
        client = app.test_client()
        order_id, task_id = prepare_delivery(client)
        result = simulate_vision(client, task_id)
        package_id = result['package']['packageId']
        print('PASS: order received -> dispatched -> simulated door cycle -> simulated vision -> ready for pickup')
        if not args.leave_ready:
            checked(client.post('/api/user/open_door_by_package',
                headers={'Authorization': 'Bearer mock_user_a'}, json={'packageId': package_id}))
            package = db.session.get(Package, package_id)
            assert package.status == 'picked'
            assert db.session.get(Box, package.box_no).status == 'empty'
            assert db.session.get(PlatformOrder, order_id).status == 'completed'
            print('PASS: recipient pickup -> order completed -> locker available')
        print('Simulation only: no physical lock, camera, model, SMS or external delivery platform was used.')
        print('Inspect the generated records at http://127.0.0.1:5000/admin/login')


if __name__ == '__main__':
    main()
