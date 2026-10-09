import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings


class RawStore:
    def __init__(self, service: BlobServiceClient, tenant_id: str):
        self.service = service
        self.tenant_id = str(UUID(tenant_id))

    def key(self, record_id: str, version: int) -> str:
        if type(version) is not int or version < 1:
            raise ValueError("Record version must be a positive integer")
        return f"{self.tenant_id}/{UUID(record_id)}/version={version}.json"

    def contains(self, record_id: str, version: int) -> bool:
        return self.service.get_blob_client("control", self.key(record_id, version)).exists()

    def persist(self, snapshot: dict[str, Any]) -> None:
        record = snapshot["record"]
        key = self.key(record["id"], record["version"])
        envelope = {
            "tenant_id": self.tenant_id,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "parser_version": "1",
            **snapshot,
        }
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode()
        digest = hashlib.sha256(body).hexdigest()
        raw = self.service.get_blob_client("bronze", f"callrecords/{key}")
        try:
            raw.upload_blob(
                body,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
                metadata={"sha256": digest},
            )
        except ResourceExistsError:
            # A retry can arrive after the raw write but before the ledger write.
            digest = raw.get_blob_properties().metadata["sha256"]
        manifest = json.dumps(
            {
                "tenant_id": self.tenant_id,
                "call_record_id": record["id"],
                "graph_version": record["version"],
                "blob_name": raw.blob_name,
                "sha256": digest,
            },
            separators=(",", ":"),
        )
        try:
            self.service.get_blob_client("control", key).upload_blob(
                manifest,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except ResourceExistsError:
            pass
