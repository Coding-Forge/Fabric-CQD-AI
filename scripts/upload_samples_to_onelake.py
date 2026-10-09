"""Upload local probe samples to a OneLake Lakehouse Bronze folder using the same layout as the ingestion Function.

Writes Files/bronze/callrecords/<tenant>/<call-id>/version=<n>.json, never overwriting an existing file.
Authenticates as the application (service principal) using the exported my_secret environment variable.
Samples contain identified telemetry; run only against an approved workspace.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from uuid import UUID

import requests
from azure.identity import AzureAuthorityHosts, ClientSecretCredential

ONELAKE_HOST = os.environ.get("ONELAKE_HOST")


def upload(session, url, body):
    create = session.put(f"{url}?resource=file", headers={"If-None-Match": "*"}, timeout=60)
    if create.status_code in (409, 412):
        return "exists"
    create.raise_for_status()
    append = session.patch(f"{url}?action=append&position=0", data=body, timeout=120)
    append.raise_for_status()
    flush = session.patch(f"{url}?action=flush&position={len(body)}", timeout=60)
    flush.raise_for_status()
    return "uploaded"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", default=os.environ.get("FABRIC_TENANT_ID"))
    parser.add_argument("--client-id", default=os.environ.get("FABRIC_CLIENT_ID"))
    parser.add_argument("--workspace", default=os.environ.get("FABRIC_WORKSPACE"), help="Workspace name or ID")
    parser.add_argument("--lakehouse", default=os.environ.get("FABRIC_LAKEHOUSE"))
    parser.add_argument("--samples", default="samples")
    parser.add_argument("--scope", default="https://storage.azure.com/.default")
    args = parser.parse_args()
    if not (args.tenant_id and args.client_id and args.workspace and args.lakehouse and ONELAKE_HOST):
        parser.error("Set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, FABRIC_WORKSPACE, FABRIC_LAKEHOUSE and "
                     "ONELAKE_HOST (or pass the matching options)")

    secret = os.environ.get("my_secret")
    if not secret:
        sys.exit("Export my_secret (non-empty) before running")
    tenant = str(UUID(args.tenant_id))

    latest = {}
    for path in glob.glob(os.path.join(args.samples, "probe-*", "record-*.json")):
        with open(path, encoding="utf-8") as handle:
            snap = json.load(handle)
        key = (str(UUID(snap["record"]["id"])), snap["record"]["version"])
        if key not in latest or snap["retrieved_at"] > latest[key]["retrieved_at"]:
            latest[key] = snap
    if not latest:
        sys.exit("No samples found")

    credential = ClientSecretCredential(
        tenant, args.client_id, secret, authority=AzureAuthorityHosts.AZURE_GOVERNMENT
    )
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {credential.get_token(args.scope).token}"
    base = f"https://{ONELAKE_HOST}/{args.workspace}/{args.lakehouse}.Lakehouse/Files/bronze/callrecords/{tenant}"

    for (record_id, version), snap in sorted(latest.items()):
        envelope = {
            "tenant_id": tenant,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "parser_version": "1",
            **snap,
        }
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode()
        status = upload(session, f"{base}/{record_id}/version={version}.json", body)
        print(f"{status}: call {record_id[:8]}... version={version}")


if __name__ == "__main__":
    main()
