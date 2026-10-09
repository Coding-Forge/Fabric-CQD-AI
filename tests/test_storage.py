import json
import unittest
from unittest.mock import Mock

from azure.core.exceptions import ResourceExistsError

from cqd.storage import RawStore


TENANT_ID = "00000000-0000-0000-0000-000000000002"
RECORD_ID = "00000000-0000-0000-0000-000000000001"


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.raw = Mock(blob_name="callrecords/synthetic.json")
        self.ledger = Mock()
        self.service.get_blob_client.side_effect = lambda container, key: (
            self.raw if container == "bronze" else self.ledger
        )
        self.store = RawStore(self.service, TENANT_ID)
        self.snapshot = {"record": {"id": RECORD_ID, "version": 2}, "responses": []}

    def test_raw_precedes_ledger_and_has_source_metadata(self):
        events = []
        self.raw.upload_blob.side_effect = lambda *args, **kwargs: events.append("raw")
        self.ledger.upload_blob.side_effect = lambda *args, **kwargs: events.append("ledger")
        self.store.persist(self.snapshot)
        self.assertEqual(events, ["raw", "ledger"])
        envelope = json.loads(self.raw.upload_blob.call_args.args[0])
        self.assertEqual(envelope["tenant_id"], TENANT_ID)
        self.assertEqual(envelope["record"], self.snapshot["record"])
        self.assertFalse(self.raw.upload_blob.call_args.kwargs["overwrite"])

    def test_failed_raw_write_cannot_mark_complete(self):
        self.raw.upload_blob.side_effect = RuntimeError("storage unavailable")
        with self.assertRaises(RuntimeError):
            self.store.persist(self.snapshot)
        self.ledger.upload_blob.assert_not_called()

    def test_retry_after_raw_write_uses_existing_hash(self):
        self.raw.upload_blob.side_effect = ResourceExistsError("exists")
        self.raw.get_blob_properties.return_value = Mock(metadata={"sha256": "existing-hash"})
        self.store.persist(self.snapshot)
        manifest = json.loads(self.ledger.upload_blob.call_args.args[0])
        self.assertEqual(manifest["sha256"], "existing-hash")

    def test_duplicate_ledger_is_idempotent(self):
        self.raw.upload_blob.side_effect = ResourceExistsError("exists")
        self.raw.get_blob_properties.return_value = Mock(metadata={"sha256": "existing-hash"})
        self.ledger.upload_blob.side_effect = ResourceExistsError("exists")
        self.store.persist(self.snapshot)

    def test_rejects_invalid_versions(self):
        for version in (0, -1, True, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.store.key(RECORD_ID, version)


if __name__ == "__main__":
    unittest.main()
