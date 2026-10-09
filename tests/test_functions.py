import json
import os
import unittest
from unittest.mock import Mock, patch

import function_app


TENANT_ID = "00000000-0000-0000-0000-000000000002"
RECORD_ID = "00000000-0000-0000-0000-000000000001"


class FunctionTests(unittest.TestCase):
    def setUp(self):
        self.graph = Mock()
        self.store = Mock(tenant_id=TENANT_ID)
        self.store.contains.return_value = False
        self.services = patch.object(function_app, "services", return_value=(Mock(), self.graph, self.store))
        self.services.start()
        self.addCleanup(self.services.stop)

    def message(self, tenant=TENANT_ID, version=1):
        return Mock(
            get_body=Mock(
                return_value=json.dumps(
                    {"tenant_id": tenant, "record_id": RECORD_ID, "version": version}
                ).encode()
            )
        )

    def test_duplicate_does_not_fetch(self):
        self.store.contains.return_value = True
        function_app.fetch_record(self.message())
        self.graph.snapshot.assert_not_called()
        self.store.persist.assert_not_called()

    def test_newer_snapshot_is_persisted(self):
        snapshot = {"record": {"id": RECORD_ID, "version": 2}, "responses": []}
        self.graph.snapshot.return_value = snapshot
        function_app.fetch_record(self.message(version=1))
        self.store.persist.assert_called_once_with(snapshot)

    def test_older_snapshot_is_not_persisted(self):
        self.graph.snapshot.return_value = {"record": {"id": RECORD_ID, "version": 1}}
        with self.assertRaisesRegex(ValueError, "older"):
            function_app.fetch_record(self.message(version=2))
        self.store.persist.assert_not_called()

    def test_other_tenant_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "tenant"):
            function_app.fetch_record(self.message(tenant=RECORD_ID))
        self.graph.snapshot.assert_not_called()

    def test_storage_failure_propagates_for_message_retry(self):
        self.graph.snapshot.return_value = {"record": {"id": RECORD_ID, "version": 1}}
        self.store.persist.side_effect = RuntimeError("storage unavailable")
        with self.assertRaises(RuntimeError):
            function_app.fetch_record(self.message())

    def test_reconciler_enqueues_only_missing_version(self):
        self.graph.list_records.return_value = [
            {"id": RECORD_ID, "version": 1},
            {"id": RECORD_ID, "version": 2},
        ]
        self.store.contains.side_effect = [True, False]
        with patch.dict(
            os.environ,
            {
                "ServiceBusConnection__fullyQualifiedNamespace": "synthetic.servicebus.usgovcloudapi.net",
                "CQD_QUEUE_NAME": "callrecords",
                "CQD_LOOKBACK_DAYS": "1",
            },
        ), patch.object(function_app, "ServiceBusClient") as factory:
            sender = factory.return_value.__enter__.return_value.get_queue_sender.return_value.__enter__.return_value
            function_app.reconcile(Mock(past_due=False))
            self.assertEqual(sender.send_messages.call_count, 1)
            sent = sender.send_messages.call_args.args[0]
            self.assertEqual(sent.message_id, f"{TENANT_ID}:{RECORD_ID}:2")


if __name__ == "__main__":
    unittest.main()
