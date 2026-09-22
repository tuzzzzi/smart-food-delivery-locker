"""Software integration tests; physical I/O and successful vision are simulated."""
import os
from pathlib import Path
import tempfile
import unittest

# Configure before importing the application: tests never use the project database.
_sandbox = tempfile.TemporaryDirectory(prefix='locker-test-')
os.environ.update({
    'DATABASE_URL': 'sqlite:///' + (Path(_sandbox.name) / 'test.db').as_posix(),
    'ARTIFACTS_DIR': str(Path(_sandbox.name) / 'artifacts'),
    'USE_MOCK': 'true', 'LOCKER_CONTROL_MODE': 'mock',
    'LOCKER_MOCK_AUTO_COMPLETE': 'true', 'PLATFORM_PUSH_MODE': 'mock',
    'AI_YOLO_ROOT': str(Path(_sandbox.name) / 'no-yolo'),
})

from app import app
from demo import checked, prepare_delivery, simulate_vision
from extensions import db
from models import Box, Package, PlatformOrder
from seed import seed_boxes
from services.mock_identities import ensure_mock_identities


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            ensure_mock_identities()
            seed_boxes()
        self.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.engine.dispose()
        _sandbox.cleanup()

    def test_admin_requires_identity(self):
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        response = self.client.post('/admin/login', data={'token': 'mock_admin_main'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_courier_cannot_use_another_courier_task(self):
        _, task_id = prepare_delivery(self.client)
        response = self.client.get('/api/courier/task', query_string={'taskId': task_id},
            headers={'Authorization': 'Bearer mock_courier_b'})
        self.assertEqual(response.status_code, 403)

    def test_missing_snapshot_does_not_create_package(self):
        _, task_id = prepare_delivery(self.client)
        response = self.client.post('/api/ai/verify_box', json={'taskId': task_id})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()['retryable'])
        with app.app_context():
            self.assertEqual(Package.query.count(), 0)

    def test_delivery_pickup_and_repeat_pickup(self):
        order_id, task_id = prepare_delivery(self.client)
        verified = simulate_vision(self.client, task_id)
        package_id = verified['package']['packageId']
        with app.app_context():
            package = db.session.get(Package, package_id)
            self.assertEqual(package.status, 'pending')
            self.assertEqual(db.session.get(Box, package.box_no).status, 'occupied')
        response = self.client.post('/api/user/open_door_by_package',
            headers={'Authorization': 'Bearer mock_user_a'}, json={'packageId': package_id})
        checked(response)
        with app.app_context():
            package = db.session.get(Package, package_id)
            self.assertEqual(package.status, 'picked')
            self.assertEqual(db.session.get(Box, package.box_no).status, 'empty')
            self.assertEqual(db.session.get(PlatformOrder, order_id).status, 'completed')
        repeated = self.client.post('/api/user/open_door_by_package',
            headers={'Authorization': 'Bearer mock_user_a'}, json={'packageId': package_id})
        self.assertEqual(repeated.status_code, 409)
        with app.app_context():
            self.assertEqual(Package.query.count(), 1)

    def test_other_recipient_cannot_pickup_package(self):
        _, task_id = prepare_delivery(self.client)
        result = simulate_vision(self.client, task_id)
        package_id = result['package']['packageId']
        response = self.client.post('/api/user/open_door_by_package',
            headers={'Authorization': 'Bearer mock_user_b'}, json={'packageId': package_id})
        self.assertEqual(response.status_code, 403)
        with app.app_context():
            self.assertEqual(db.session.get(Package, package_id).status, 'pending')


if __name__ == '__main__':
    unittest.main()
