import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from itertools import islice
from uuid import UUID

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import AzureAuthorityHosts, ClientSecretCredential
from azure.storage.blob import BlobServiceClient

from cqd.graph import GRAPH_SCOPE, GraphClient, GraphError
from cqd.probe import bounded_integer, read_secret
from cqd.storage import RawStore


def ingest(graph: GraphClient, store: RawStore, hours: int, limit: int | None) -> dict[str, int]:
    end = datetime.now(timezone.utc)
    records = graph.list_records(end - timedelta(hours=hours), end)
    counts = {"listed": 0, "stored": 0, "skipped": 0}
    for record in islice(records, limit):
        counts["listed"] += 1
        record_id = str(UUID(record["id"]))
        if store.contains(record_id, record["version"]):
            counts["skipped"] += 1
            continue
        snapshot = graph.snapshot(record_id)
        store.persist(snapshot)
        counts["stored"] += 1
        if counts["stored"] % 25 == 0:
            print(f"Stored {counts['stored']} snapshots so far")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull Graph call records and write immutable Bronze snapshots to ADLS Gen2."
    )
    parser.add_argument("--tenant-id", default=os.environ.get("FABRIC_TENANT_ID"),
                        help="Defaults to the FABRIC_TENANT_ID environment variable")
    parser.add_argument("--client-id", default=os.environ.get("FABRIC_CLIENT_ID"),
                        help="Defaults to the FABRIC_CLIENT_ID environment variable")
    parser.add_argument("--account", default=os.environ.get("CQD_STORAGE_ACCOUNT"),
                        help="Storage account name; defaults to the CQD_STORAGE_ACCOUNT environment variable")
    parser.add_argument("--hours", type=bounded_integer(1, 720), default=24)
    parser.add_argument("--limit", type=bounded_integer(1, 1_000_000), default=None)
    args = parser.parse_args()
    if not (args.tenant_id and args.client_id and args.account):
        parser.error("Provide --tenant-id, --client-id and --account, or set FABRIC_TENANT_ID, "
                     "FABRIC_CLIENT_ID and CQD_STORAGE_ACCOUNT")
    try:
        tenant_id = str(UUID(args.tenant_id))
        client_id = str(UUID(args.client_id))
    except ValueError:
        parser.error("Tenant and client IDs must be UUIDs")
    logging.getLogger("azure").setLevel(logging.WARNING)
    secret = read_secret(parser)
    with ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=secret,
        authority=AzureAuthorityHosts.AZURE_GOVERNMENT,
    ) as credential:
        graph = GraphClient(lambda: credential.get_token(GRAPH_SCOPE).token)
        service = BlobServiceClient(
            f"https://{args.account}.blob.core.usgovcloudapi.net", credential=credential
        )
        try:
            counts = ingest(graph, RawStore(service, tenant_id), args.hours, args.limit)
        except ClientAuthenticationError:
            print("Authentication failed. Credential details were not printed.", file=sys.stderr)
            return 1
        except GraphError as error:
            print(f"Graph retrieval failed: {error}", file=sys.stderr)
            return 1
        except HttpResponseError as error:
            print(f"Storage request failed: {error.status_code} {error.error_code}. Check the "
                  "containers 'bronze' and 'control' exist and the identity has Storage Blob "
                  "Data Contributor.", file=sys.stderr)
            return 1
    print(f"Listed {counts['listed']}; stored {counts['stored']}; "
          f"already present {counts['skipped']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
