import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from uuid import UUID

import azure.functions as func
from azure.identity import AzureAuthorityHosts, DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.storage.blob import BlobServiceClient

from cqd.graph import GRAPH_SCOPE, GraphClient
from cqd.storage import RawStore


app = func.FunctionApp()
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def services():
    credential = DefaultAzureCredential(authority=AzureAuthorityHosts.AZURE_GOVERNMENT)
    graph = GraphClient(lambda: credential.get_token(GRAPH_SCOPE).token)
    store = RawStore(
        BlobServiceClient(os.environ["CQD_BLOB_URL"], credential=credential),
        os.environ["CQD_TENANT_ID"],
    )
    return credential, graph, store


@app.timer_trigger(
    schedule="0 0 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def reconcile(timer: func.TimerRequest) -> None:
    credential, graph, store = services()
    end = datetime.now(timezone.utc)
    days = int(os.environ.get("CQD_LOOKBACK_DAYS", "1"))
    if not 1 <= days <= 30:
        raise ValueError("CQD_LOOKBACK_DAYS must be between 1 and 30")
    sent = 0
    with ServiceBusClient(
        os.environ["ServiceBusConnection__fullyQualifiedNamespace"], credential
    ) as client:
        with client.get_queue_sender(os.environ["CQD_QUEUE_NAME"]) as sender:
            for record in graph.list_records(end - timedelta(days=days), end):
                record_id = str(UUID(record["id"]))
                version = record["version"]
                if store.contains(record_id, version):
                    continue
                sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(
                            {
                                "tenant_id": store.tenant_id,
                                "record_id": record_id,
                                "version": version,
                            }
                        ),
                        message_id=f"{store.tenant_id}:{record_id}:{version}",
                    )
                )
                sent += 1
    logger.info("Reconciliation completed; enqueued=%d past_due=%s", sent, timer.past_due)


@app.service_bus_queue_trigger(
    arg_name="message",
    queue_name="%CQD_QUEUE_NAME%",
    connection="ServiceBusConnection",
)
def fetch_record(message: func.ServiceBusMessage) -> None:
    _, graph, store = services()
    request = json.loads(message.get_body())
    if request["tenant_id"] != store.tenant_id:
        raise ValueError("Message tenant does not match the configured tenant")
    record_id = str(UUID(request["record_id"]))
    version = request["version"]
    if store.contains(record_id, version):
        logger.info("Duplicate record version skipped")
        return
    snapshot = graph.snapshot(record_id)
    if snapshot["record"]["version"] < version:
        raise ValueError("Graph returned a version older than the queued version")
    store.persist(snapshot)
    logger.info("Call-record snapshot persisted")
