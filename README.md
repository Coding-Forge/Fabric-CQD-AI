# CQD Analytics POC

Python Azure Functions ingestion foundation for Microsoft 365 GCC High and Azure Government. This is prepared code, not a deployed or customer-accepted solution.

## Approved preparation targets

| Target | Selection |
| ------ | --------- |
| Azure cloud | AzureUSGovernment |
| Resource group | `<resource-group>` |
| Region | usgovvirginia |
| Teams source | GCC High |
| Graph / authority | `https://graph.microsoft.us` / `https://login.microsoftonline.us` |
| Fabric destination requested | `<fabric-capacity>` in `<fabric-resource-group>`; cloud, SKU, workspace, and connectivity unverified |
| Deployment authorization | Prepare code and infrastructure only; do not deploy |

Supply tenant/subscription IDs through approved local configuration or deployment parameters, not source files. Do not commit credentials, raw call records, or identified sample data.

## Read-only API connectivity spike

Read-only GCC High Graph requests and a small raw sample saved in the Git-ignored `samples/` folder are authorized. This does not authorize Azure deployment or permission changes. A CQD report is not required for this access spike; CQD reporting equivalence remains unproven.

The user reported granting the required permission on October 7, 2026. Before running the probe, confirm Microsoft Graph **application** permission `CallRecords.Read.All` has tenant-wide administrator consent. Delegated `User.Read` and an ordinary Azure CLI login are insufficient.

Revoke any credential disclosed in chat and create a replacement through the approved process. Never paste replacement secrets into chat, command arguments, source files, or notebook cells. This local probe reads an exported `my_secret` environment variable when present; otherwise it prompts without echoing or saving the replacement secret. An empty `my_secret` is an explicit error. Managed identity remains the intended Azure-hosted authentication method.

If `my_secret` is already set in your shell, run `export my_secret` in that same terminal before running the probe. Do not print its value or persist it in shell startup files. Run `unset my_secret` after testing when it is no longer needed.

From this folder, after permissions and the replacement credential are ready:

```bash
.venv/bin/python -m cqd.probe \
  --tenant-id YOUR_GCC_HIGH_TENANT_ID \
  --client-id YOUR_APPLICATION_CLIENT_ID \
  --hours 24 \
  --limit 3
```

The probe makes read-only Graph requests and saves at most three expanded snapshots by default (maximum ten). It prints only sample counts, session/participant counts, and the local storage path. Sample directories are owner-only (`0700`) and files are owner-only (`0600`); existing public directories or symlinked sample roots are rejected. Samples contain identified telemetry: do not upload or commit them, and delete them according to the approved retention policy.

An empty window proves the list request succeeded, not that expanded records were retrieved. An HTTP 403 is a failure requiring permission/consent investigation, not a successful connection test. HTTP failures report the operation, a format-validated Graph error code, and a validated request ID; raw server messages and call identifiers are not printed. HTTP 400 reports a rejected request, not automatic permission advice. A candidate progress message confirms a record was listed before expanded retrieval started.

Live access was verified on October 8, 2026: the probe authenticated with `CallRecords.Read.All`, listed call records, and retrieved expanded records with sessions, segments, media streams, and participants. Earlier on October 7, 2026, a live listing returned an empty window and later runs reported HTTP 400; that HTTP 400 did not recur on October 8 and its cause was not investigated. Use `--diagnose-list` if it returns.

For confirmed listing-stage HTTP 400 responses, append `--diagnose-list` to the probe command. This mode compares four independent first-page requests: unfiltered, the existing offset timestamp format, UTC `Z` with identical bounds, and UTC `Z` with both bounds rounded down to whole seconds. The first three share one captured time window where applicable; the last differs only at subsecond boundaries. It reports first-page counts and pagination presence, not complete tenant totals, and does not fetch details, follow additional pages, or save raw data. `--limit` applies only to normal sample collection, not this first-page comparison. A failed case remains explicitly failed and causes a nonzero exit even if other cases succeed; this is not an ingestion fallback or a fix for the unconfirmed root cause.

Local probe validation: the initial `.venv/bin/python -m unittest discover -s tests -v` run passed all 30 synthetic tests, and `.venv/bin/python -m cqd.probe --help` passed. Environment-variable support adds tests for exported, absent, and empty credentials. These checks do not authenticate to Graph.

## Local Power BI exploration

`scripts/flatten_samples.py` merges the probe samples (latest snapshot per call version) into pseudonymized CSVs in the Git-ignored `samples/powerbi/` folder: `Calls`, `Participants`, `Sessions`, `Segments`, and `MediaStreams`. Users appear only as truncated SHA-256 hashes; names, UPNs, and IP addresses are omitted.

```bash
.venv/bin/python scripts/flatten_samples.py
```

`PBIP-Local/CQD.pbip` is a Power BI Project (semantic model plus a two-page report) over those CSVs. Open it in Power BI Desktop and edit the `SamplesFolder` parameter to the absolute path of `samples/powerbi` on your machine; the committed value is a WSL path. Rerun the probe and flatten script, then refresh, to see new calls.

The stream quality classification (Good/Fair/Poor) uses illustrative thresholds and is not verified CQD reporting equivalence. This is an exploratory view of sample data, not a production report.

`notebooks/cqd_bronze_to_silver_gold.ipynb` is the Fabric (PySpark) port of the same flattening logic. Import it into the approved workspace, attach a Lakehouse, set `BRONZE_ROOT`, `TENANT_ID`, and `USER_HASH_SALT` (from an approved secret store), and run. It reads Bronze snapshots modified within `LOOKBACK_HOURS`, keeps the latest version per call in `silver_*` Delta tables, and rebuilds `gold_stream_quality` and `gold_call_summary` for Direct Lake. On October 8, 2026 the notebook ran end to end in Fabric Government against 3 sample calls read through a Lakehouse shortcut (`Files/bronze/callrecords` to `<storage-account>/bronze/callrecords`), producing 3 calls, 6 participants, 6 sessions, 6 segments, 36 media streams, 36 gold stream rows and 3 gold call summaries. A fourth call was then ingested with `cqd.ingest` and loaded incrementally through the shortcut (4 calls, 8 sessions, 48 media streams), leaving earlier rows unchanged.

Fabric Government native Git integration is not assumed. Notebook code may be copied from the repository into the approved workspace for the POC, recording its source commit.

### Direct Lake semantic model (built in the Fabric UI)

The `CQD` semantic model is created from the Lakehouse in the Fabric service, not from `PBIP-Local` (local Desktop cannot host Direct Lake, and calculated columns are unsupported, so `StreamQuality` and `CallLabel` are materialized in Gold). Tables: `gold_call_summary`, `gold_stream_quality`, `silver_sessions`, `silver_participants`, `silver_segments`. Relationships are all one-to-many with single-direction filtering: `gold_call_summary[CallId]` to `silver_sessions[CallId]` and `silver_participants[CallId]`, and `silver_sessions[SessionId]` to `silver_segments[SessionId]` and `gold_stream_quality[SessionId]`. `CallLabel` sorts by `StartDateTime`. Measures such as Call Count, Session Count, Stream Count, Avg Jitter, Avg Round Trip, Avg Packet Loss %, and Good Stream % are defined in the model. The model definition is not stored in this repository; recreate it from this description. Verified on October 8, 2026: per-call report values differ and total to 4 calls, 8 sessions, and 48 streams. `CallLabel` times are UTC. The `PBIP-Local` CSV report remains as a local-only exploration copy.

## Manual ingestion to ADLS Gen2 (POC)

`cqd.ingest` pulls call records as the service principal and writes immutable Bronze snapshots through `RawStore`. It skips versions already in the `control` ledger, so reruns are safe. The account needs `bronze` and `control` containers and **Storage Blob Data Contributor** for the identity. Verified on October 8, 2026 with one record written to the storage account.

```bash
.venv/bin/python -m cqd.ingest --tenant-id <tenant> --client-id <client> --account <storage-account> --hours 168 # or set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, CQD_STORAGE_ACCOUNT
```

Export `my_secret` first and run `unset my_secret` afterward. This is a manual POC path; the Functions pipeline below remains the scheduled design.

### Scheduled POC ingest notebook

`notebooks/cqd_ingest_graph_to_bronze.ipynb` runs the same `cqd.ingest` logic inside Fabric. Upload the repo's `cqd/` folder to the Lakehouse at `Files/code/cqd`, keep the SPN secret in Key Vault (read via the Gov vault REST API with a `notebookutils.credentials.getToken` token, because `getSecret` rejects `vault.usgovcloudapi.net`), and set the parameters cell. Data Pipeline `cqd_pipeline` runs it ahead of `cqd_bronze_to_silver_gold` (Ingest, then Transform on success) and is scheduled hourly for the POC with a lookback of 24 hours; the ledger makes the overlap safe. Verified on October 9, 2026: the Gov Key Vault REST read, Fabric egress to `graph.microsoft.us`, and a full manual pipeline run (about 8 minutes) all succeeded. The first unattended scheduled run is not yet confirmed. The enterprise Functions design replaces it after approval.

## Chat over the Silver and Gold tables (POC)

`chat/` is a local Q&A page that proves the Lakehouse can feed an AI chat app. It reads only the curated **Silver and Gold Delta tables** (`silver_*`, `gold_*` under `Tables/dbo`), never the Bronze files or the `Files/bronze/callrecords` shortcut. The server lists the `silver_*` and `gold_*` tables through the OneLake API, replays each `_delta_log` to find the active parquet files, reads them with `pyarrow` into in-memory SQLite, and lets Azure OpenAI answer by running read-only `SELECT`s. Each answer shows the SQL, the parquet files read, and a **heuristic confidence score** (query success, answer numbers found in the results, source completeness such as NULL stream metrics). The score is not a measured accuracy. The tables are only as fresh as the last pipeline run.

```bash
.venv/bin/pip install -r chat/requirements.txt
export FABRIC_TENANT_ID=<tenant> FABRIC_CLIENT_ID=<client> FABRIC_CLIENT_SECRET=<from secret store, not shell history>
export ONELAKE_HOST=<onelake-dfs-host> FABRIC_WORKSPACE=<workspace> FABRIC_LAKEHOUSE=<lakehouse>   # put these in ~/.bashrc
export AZURE_OPENAI_ENDPOINT=<endpoint> AZURE_OPENAI_API_KEY=<key>
.venv/bin/python -m chat.server --check   # reads the shortcut only; no model needed
.venv/bin/python -m chat.server           # http://127.0.0.1:8000
```

`--check` was verified on October 9, 2026: 7 Silver/Gold tables read (5 calls, 60 streams), matching the report. The model path is covered by a fake client in `tests/test_chat.py` and has not been run against a real Azure OpenAI deployment. Delta tables with checkpoints or deletion vectors are reported as unreadable rather than guessed. The server binds to localhost only. Optional overrides: `LAKEHOUSE_SCHEMA` (`dbo`), `AZURE_OPENAI_DEPLOYMENT`, `CHAT_PORT`.

## Current implementation

An hourly reconciler lists an overlapping one-day call-start window and enqueues missing record versions in Service Bus. The queue worker retrieves expanded sessions and segments, separately retrieves `participants_v2`, follows collection and nested pagination, and checks the parent version again before storing the snapshot.

Each snapshot preserves normalized hierarchy plus exact successful Graph response bodies, request IDs, and retrieval timestamps. Bronze objects are keyed by tenant, record ID, and version and are never overwritten by the application. A control manifest is written only after the raw object is durable; that manifest acts as the per-version ingestion ledger. Retries and duplicate messages are safe across the two writes. This is application-level immutability, not a storage-enforced WORM policy.

Authentication uses the Function App's managed identity, GCC High Graph scope, and Azure Government service endpoints. The identity needs the `CallRecords.Read.All` application role granted by an authorized tenant administrator; Azure RBAC does not grant Graph permissions.

The template prepares ADLS, separate Functions host storage, Service Bus Standard, a single-instance Linux B1 App Service plan, a Python 3.11 Function App, and scoped data-plane roles. It derives storage endpoints from deployed resources and uses the Government Service Bus suffix. **Both functions are disabled by default.** B1 is a proposed POC hosting choice with ongoing charges, not approved production sizing. Confirm regional availability and pricing before deployment.

## Local validation

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
az bicep build --file infra/main.bicep --outfile infra/main.json
```

Tests use synthetic data and mocked services; they do not contact Azure or Graph. The generated ARM template is ignored by Git. Local Python may differ from the proposed Azure Python 3.11 runtime; validate that runtime before deployment.

Preparation evidence on October 7, 2026: all 25 unit tests passed; Bicep compilation passed without template diagnostics; Function registration returned `reconcile` and `fetch_record`; `pip check` passed. Tests ran on local Python 3.13.11, with a separate Python 3.11 syntax check; this is not Python 3.11 runtime or Azure integration evidence. Git whitespace checking uses `cr-at-eol` because the original exploration document has CRLF line endings.

## Deployment gates

Do not deploy or enable collection until:

1. The customer approves source mapping, POC scope, sample handling, retention, and the proposed hosting cost.
2. The deployment subscription tenant matches the GCC High source tenant, or a separately approved cross-tenant authentication design replaces managed identity.
3. Azure Government regional provider/SKU availability, host identity-based storage, and service connectivity are validated.
4. The administrator approves and grants the Graph application role.
5. Required network restrictions and monitoring are designed and approved.
6. The actual Fabric capacity, workspace, cloud availability, and permitted ADLS access are verified; do not assume commercial Fabric can receive Government telemetry.
7. A Git remote is chosen and explicitly approved for pushing this repository.

After authorization, use AzureUSGovernment in the deployment shell, select the approved subscription, and deploy `infra/main.bicep` to the approved resource group with `namePrefix` and `sourceTenantId`. Publish Python code separately, verify settings and roles, then enable functions only after access and governance checks. No deployment command is executed by this repository automatically.

## Explicitly unfinished

- Customer report inventory, CQD-to-Graph mapping, and stakeholder acceptance.
- Change notifications, subscription creation/renewal, and lifecycle handling. Polling is the initial retrieval spike, not the complete production architecture.
- Full 30-day reconciliation/backfill orchestration. The one-day default intentionally limits initial collection; it cannot recover older gaps without an approved wider window.
- Guaranteed preservation of all historical source versions. Graph returns the available current version; versions that were never retrieved cannot be reconstructed.
- PSTN / Direct Routing collectors, raw compaction, schema quarantine, and operational alerting.
- Private endpoints and network isolation. The proposed template exposes authenticated public service endpoints and is not approved for live identified data.
- Storage lifecycle rules and restricted operational retention.
- Fabric notebooks, Silver/Gold models, and Power BI reports. These require the approved source mapping and destination confirmation.
- Live integration, recovery, volume, Python 3.11, and report-performance validation.

The [delivery checklist](CQD-Fabric-Delivery-Checklist.md) remains the acceptance tracker; source and production gates are not satisfied by compiling code.

## Source references

- [Graph national cloud endpoints](https://learn.microsoft.com/en-us/graph/deployments)
- [List call records and national-cloud availability](https://learn.microsoft.com/en-us/graph/api/callrecords-cloudcommunications-list-callrecords?view=graph-rest-1.0)
- [Retrieve call records, expansion, and pagination](https://learn.microsoft.com/en-us/graph/api/callrecords-callrecord-get?view=graph-rest-1.0)
- [Azure Government differences](https://learn.microsoft.com/en-us/azure/azure-government/compare-azure-government-global-azure)
