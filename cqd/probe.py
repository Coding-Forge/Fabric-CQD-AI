import argparse
import getpass
import json
import logging
import os
import stat
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from uuid import UUID

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureAuthorityHosts, ClientSecretCredential

from cqd.graph import GRAPH_ROOT, GRAPH_SCOPE, GraphClient, GraphError, GraphHttpError


def bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Expected an integer") from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Expected a value between {minimum} and {maximum}")
        return number
    return parse


def read_secret(parser: argparse.ArgumentParser) -> str:
    secret = os.environ.get("my_secret")
    if secret is None:
        if not sys.stdin.isatty():
            parser.error("Export my_secret or run in an interactive terminal for the hidden prompt")
        secret = getpass.getpass("Replacement app secret (hidden; never saved): ")
    if not secret:
        parser.error("The application credential is empty; populate my_secret or the hidden prompt")
    return secret


def sample_directory(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("Sample directory must not be a symbolic link")
    root.mkdir(mode=0o700, exist_ok=True)
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise ValueError("Sample directory must be private to its owner (mode 0700)")
    return Path(tempfile.mkdtemp(prefix="probe-", dir=root))


def collect(graph: GraphClient, tenant_id: str, hours: int, limit: int, root: Path) -> int:
    tenant_id = str(UUID(tenant_id))
    end = datetime.now(timezone.utc)
    folder = None
    count = 0
    for record in islice(graph.list_records(end - timedelta(hours=hours), end), limit):
        print(f"Listed sample candidate {count + 1}; requesting expanded record and participants.")
        snapshot = graph.snapshot(record["id"])
        if folder is None:
            folder = sample_directory(root)
        target = folder / f"record-{count + 1:03d}.json"
        envelope = {
            "tenant_id": tenant_id,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            **snapshot,
        }
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(envelope, output, ensure_ascii=True, separators=(",", ":"))
        count += 1
        print(f"Saved sample {count}; sessions={len(snapshot['record']['sessions'])}; "
              f"participants={len(snapshot['record']['participants_v2'])}")
    if count:
        print(f"Retrieved {count} record snapshots. Restricted raw samples: {folder}")
    else:
        print("Graph list request succeeded, but no records matched the call-start window. "
              "Expanded record access has not been demonstrated.")
    return count


def diagnose_listing(graph: GraphClient, hours: int) -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    cases = (
        ("unfiltered (available retention window)", f"{GRAPH_ROOT}/v1.0/communications/callRecords"),
        ("offset UTC (current probe format)", GraphClient.record_list_url(start, end)),
        ("UTC Z (same exact bounds)", GraphClient.record_list_url(start, end, utc_z=True)),
        (
            "UTC Z (whole-second bounds)",
            GraphClient.record_list_url(
                start.replace(microsecond=0), end.replace(microsecond=0), utc_z=True
            ),
        ),
    )
    print("Listing diagnostic: first page only; no expanded records or raw samples are saved.")
    print(f"Captured UTC window: {start.isoformat()} to {end.isoformat()}")
    failures = 0
    for label, url in cases:
        try:
            payload = graph.get(url)
            values = payload.get("value")
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise GraphError("Graph collection is missing an object array")
            next_link = payload.get("@odata.nextLink", "")
            if not isinstance(next_link, str):
                raise GraphError("Graph nextLink must be a string")
            print(f"{label}: HTTP 200; first_page_records={len(values)}; "
                  f"more_pages={'yes' if next_link else 'no'}")
        except GraphError as error:
            failures += 1
            print(f"{label}: FAILED; {error}")
    print("First-page counts are not tenant-wide totals. Empty lists do not prove that "
          "the meeting never occurred. Failed cases are not retried with a different filter.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only GCC High call-record probe; saves restricted local samples."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--hours", type=bounded_integer(1, 720), default=24)
    parser.add_argument("--limit", type=bounded_integer(1, 10), default=3)
    parser.add_argument(
        "--diagnose-list",
        action="store_true",
        help="Compare unfiltered and timestamp-formatted first-page listings without saving data",
    )
    args = parser.parse_args()
    try:
        tenant_id = str(UUID(args.tenant_id))
        client_id = str(UUID(args.client_id))
    except ValueError:
        parser.error("Tenant and client IDs must be UUIDs")
    logging.getLogger("azure").setLevel(logging.WARNING)
    secret = read_secret(parser)
    root = Path(__file__).resolve().parent.parent / "samples"
    with ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=secret,
        authority=AzureAuthorityHosts.AZURE_GOVERNMENT,
    ) as credential:
        graph = GraphClient(lambda: credential.get_token(GRAPH_SCOPE).token)
        try:
            if args.diagnose_list:
                return diagnose_listing(graph, args.hours)
            collect(graph, tenant_id, args.hours, args.limit, root)
        except ClientAuthenticationError:
            print("Application authentication failed. Check tenant, client ID, credential, "
                  "and GCC High authority. Credential details were not printed.", file=sys.stderr)
            return 1
        except GraphError as error:
            print(f"Retrieval failed: {error}", file=sys.stderr)
            if isinstance(error, GraphHttpError):
                if error.status == 403:
                    print("Check CallRecords.Read.All application permission and administrator consent.",
                          file=sys.stderr)
                elif error.status == 401:
                    print("Check application authentication and the GCC High token audience.",
                          file=sys.stderr)
                elif error.status == 400:
                    print("The API rejected the reported request; HTTP 400 alone does not "
                          "indicate missing permissions.", file=sys.stderr)
            print("Any earlier samples remain local. No raw error body or call identifiers "
                  "were printed.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
