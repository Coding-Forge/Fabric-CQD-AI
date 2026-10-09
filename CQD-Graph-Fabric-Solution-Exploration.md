# CQD, Microsoft Graph, ADLS Gen2, and Microsoft Fabric Solution Exploration

**Prepared:** October 7, 2026\
**Status:** Exploration and proof-of-concept blueprint

## Implementation context: Azure Government

The selected POC source is Microsoft 365 GCC High, with Azure infrastructure preparation targeting `<resource-group>` in `usgovvirginia`. Use `https://graph.microsoft.us` and `https://login.microsoftonline.us`, not the commercial Graph and authentication endpoints. Microsoft documents call-record list/get support in US Government L4 and L5.

The architecture below remains a reference design, not a verified Government deployment. Graph Data Connect does not support national clouds. Event Grid partner delivery and all required Azure features must be verified for the selected cloud before adoption. The requested Fabric destination is `<fabric-capacity>` in `<fabric-resource-group>`; its cloud, capacity, workspace, connectivity, and permission to receive Government telemetry remain unverified.

Only code and infrastructure preparation is authorized. The initial implementation uses disabled-by-default reconciliation and queue workers; it does not yet implement subscriptions or the complete production architecture. See the [implementation README](README.md) and [delivery checklist](CQD-Fabric-Delivery-Checklist.md).

## Executive summary

The customer currently analyzes Microsoft Teams Call Quality Dashboard (CQD) data through Power BI. Daily query results exceed 100,000 rows, making retrieval and analysis slow. The proposed direction is to establish a durable analytical platform using Azure Data Lake Storage Gen2 (ADLS Gen2) and Microsoft Fabric.

The central technical finding is:

> Microsoft Graph does not expose the same tenant-level CQD dataset, cube, dimensions, measures, classifications, or Power BI connector results.

Microsoft Graph provides the supported `callRecords` API. It exposes raw call, session, segment, media, device, network, and stream telemetry from which a new call-quality analytical model can be built. Microsoft explicitly states that Graph call records might not contain every field available in CQD and that field names can differ.

Therefore, the proposed solution is a **conditional go**:

* **Go** if the customer's required analytical outcomes can be produced from Graph call-record telemetry.
* **No-go** if exact CQD schema, calculations, classifications, enrichment, or 12-month historical parity is required.
* Fabric and ADLS Gen2 can comfortably support the anticipated volume. Source semantics and field coverage, rather than platform scale, are the main risks.

## Feasibility decision

| Requirement                                                     | Assessment                                                                    |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Retrieve the exact CQD Power BI dataset through Microsoft Graph | Not supported                                                                 |
| Build a new call-quality warehouse from Graph `callRecords`     | Feasible                                                                      |
| Automate app-only data collection                               | Supported with `CallRecords.Read.All`                                         |
| Backfill existing CQD history through Graph                     | Limited to the Graph 30-day retention window                                  |
| Preserve CQD's 12-month history                                 | Not available through Graph                                                   |
| Reproduce every CQD dimension, measure, and classification      | Not guaranteed; some are unavailable or must be derived                       |
| Ingest more than 100,000 records per day into ADLS/Fabric       | Technically modest                                                            |
| Ingest PSTN and Direct Routing information                      | Supported through separate Graph endpoints                                    |
| Use Graph Data Connect as a complete CQD replacement            | Not currently suitable; its Teams call dataset lacks media-quality/QoE detail |

## CQD and Graph are different products

### CQD

CQD is a curated tenant-wide quality-analysis service. It provides:

* A cube of dimensions and measures.
* Microsoft-defined quality classifications.
* Tenant building and network enrichment.
* Device, endpoint, network, and media-quality reporting.
* Up to 12 months of retained data.
* Enhanced user-identifiable information handling, including removal of CQD EUII fields after 28 days.
* A custom Power BI connector using DirectQuery.

The CQD Power BI connector has important limitations:

* It supports DirectQuery rather than Import mode.
* It has an API-side 10,000-row result limit per query.
* Queries require at least one CQD measure.
* It requires interactive user authentication.
* Service-principal and managed-identity refresh are not supported.

Although Microsoft documentation refers to a CQD API behind the connector, Microsoft does not publish a supported customer REST contract for unattended CQD extraction into a data lake. Reverse-engineering connector traffic would create an unsupported production dependency.

### Microsoft Graph `callRecords`

Graph exposes raw diagnostic records through:

```http
GET /v1.0/communications/callRecords
GET /v1.0/communications/callRecords/{id}
```

The Graph model is hierarchical:

```text
Call record
  ├─ Organizer
  ├─ Participants
  └─ Sessions
      └─ Segments
          └─ Media
              └─ Streams
```

Available information can include:

* Call type, modalities, organizer, participants, and timestamps.
* Caller and callee endpoints.
* Device information, CPU characteristics, and user agent.
* Network connection, transport, relay, Wi-Fi, subnet, and IP information.
* Session and segment failures.
* Audio and video codecs.
* Jitter, packet loss, and round-trip time.
* Audio degradation and concealed-sample ratios.
* Video frame rate, frame loss, and freeze duration.
* Bandwidth estimates and forward error correction.
* Media bypass indicators.
* User feedback where available.

These fields are substantial, but they do not establish CQD parity.

## Key source constraints

### Retention and backfill

* Graph call records remain available for 30 days.
* Records older than 30 days return `404 Not Found`.
* An initial deployment can backfill only the preceding 30 days.
* Graph cannot retrieve the remaining CQD history that may still be available within CQD's 12-month window.
* Recovery from an ingestion outage lasting more than 30 days is impossible through Graph.

### Authentication and permissions

The solution requires:

* An Entra ID application registration.
* Microsoft Graph application permission `CallRecords.Read.All`.
* Tenant administrator consent.
* Application authentication; delegated permissions are not supported.
* A certificate or workload identity rather than a client secret wherever possible.

### Availability latency and versions

* A call record is created after the call or meeting ends.
* The first version generally appears within 30 minutes but can take up to 150 minutes.
* Delayed client telemetry can produce later versions of the same record.
* Each call record carries a monotonically increasing `version`.
* The pipeline must retain version history and update the current analytical record when a higher version arrives.

### Paging

* The call-record list endpoint defaults to 60 records per page.
* Sessions are limited to 60 per page.
* `participants_v2` is limited to 130 per page.
* PSTN and Direct Routing functions return up to 1,000 rows per page.
* Every `@odata.nextLink` must be followed.

Use `participants_v2` and `organizer_v2`. Their older equivalents were deprecated and were scheduled to stop returning data on June 30, 2026.

### Throttling

The pipeline must honor `429 Too Many Requests` responses and the `Retry-After` header. Relevant documented cloud-communications limits include:

| Scope                              |                Published limit |
| ---------------------------------- | -----------------------------: |
| Per application across all tenants | 15,000 requests per 20 seconds |
| Per tenant across all applications | 10,000 requests per 20 seconds |
| Per application per tenant         |  1,500 requests per 20 seconds |
| Per individual call record         |     40 requests per 20 seconds |
| List call records                  |     40 requests per 20 seconds |

Limits can evolve, so the implementation must treat throttling dynamically rather than assuming fixed throughput.

## Recommended target architecture

```text
Microsoft Graph
  /communications/callRecords
            |
            | updated change notifications
            v
Azure Event Grid partner topic
            |
            +------> Subscription renewal and lifecycle Function
            |
            v
Azure Service Bus queue and dead-letter queue
            |
            v
Queue-triggered Azure Functions
  - bounded Graph request concurrency
  - pagination
  - Retry-After handling
  - record-version validation
  - raw JSON and manifest writes
            |
            v
ADLS Gen2
  - staging
  - immutable bronze
  - notifications
  - control manifests
  - quarantine
            |
            v
OneLake shortcut in a Fabric Lakehouse
            |
            v
Fabric pipeline and notebook transformations
  - normalized Delta silver tables
  - pseudonymized aggregates
  - quality measures and classifications
            |
            +------> Fabric Warehouse or SQL analytics endpoint
            |
            v
Power BI semantic model and reports
```

### Why use a Lakehouse before a Fabric Warehouse?

Graph produces nested, evolving JSON. A Fabric Lakehouse is the better landing and transformation environment for this semi-structured data. It supports:

* Direct access to ADLS through a OneLake shortcut.
* Spark-based JSON normalization.
* Delta tables and schema evolution.
* Efficient merges and version processing.
* Direct Lake semantic models.

A Fabric Warehouse can serve curated relational Gold tables if the customer prefers T-SQL consumption, governed dimensional models, or existing warehouse patterns. The recommended architecture is therefore:

```text
ADLS raw data -> Fabric Lakehouse Bronze/Silver -> Fabric Warehouse or Lakehouse Gold
```

## Ingestion design

### 1. Graph subscription

Create one tenant-wide subscription using `updated`, not only `created`:

```json
{
  "changeType": "updated",
  "resource": "/communications/callRecords",
  "notificationUrl": "EventGrid:?azuresubscriptionid=...&resourcegroup=...&partnertopic=...",
  "lifecycleNotificationUrl": "EventGrid:?azuresubscriptionid=...&resourcegroup=...&partnertopic=...",
  "expirationDateTime": "...",
  "clientState": "<random-secret>"
}
```

The maximum call-record subscription lifetime is 4,230 minutes, slightly less than three days.

Operational policy:

* Renew subscriptions every 24 hours.
* Keep at least 24 hours of remaining subscription life.
* Store subscription ID, expiration, last renewal, and renewal errors.
* Alert whenever remaining life falls below 24 hours.
* Handle reauthorization lifecycle events.

### 2. Event ingress

Event Grid is the preferred production entry point because Microsoft Graph supports delivery of change notifications to an Event Grid partner topic. Event Grid can route events to Service Bus.

Event delivery is at-least-once and unordered. Duplicate and out-of-order events are expected.

For an initial technical spike, an HTTP-triggered Azure Function can validate the webhook protocol:

1. URL-decode the Graph `validationToken`.
2. Return it unchanged as `text/plain` with HTTP 200 within 10 seconds.
3. Validate `clientState` on notification requests.
4. Place the envelope on Service Bus.
5. Return HTTP 202 within three seconds.

The webhook must not synchronously fetch or transform Graph data.

### 3. Service Bus

Use Service Bus Standard or Premium with:

* Peek-Lock processing.
* Dead-letter queue.
* Duplicate detection.
* Managed-identity authentication.
* Monitoring of queue depth and oldest-message age.

Recommended message identity:

```text
tenantId:callRecordId:version
```

Duplicate detection is only an optimization. Durable idempotency must come from the ingestion ledger and Delta merge logic.

### 4. Graph fetch workers

Queue-triggered Functions should:

1. Read the notification envelope.
2. Validate tenant, call-record ID, and version.
3. Acquire a Graph token using workload identity or a certificate.
4. Retrieve the expanded record.
5. Follow every `@odata.nextLink`.
6. Honor `429` and `Retry-After`.
7. Save the complete raw response and ingestion envelope.
8. Update the ingestion ledger.
9. Complete the Service Bus message only after durable storage succeeds.

Example request:

```http
GET /v1.0/communications/callRecords/{id}
    ?$expand=participants_v2,sessions($expand=segments)
```

Begin with 4-8 concurrent Graph requests per Function instance. Apply bounded scale-out, exponential backoff with jitter when needed, and circuit-breaking behavior during widespread throttling.

### 5. Reconciliation

Change notifications alone are insufficient. Implement a timer-driven reconciler:

1. Query `GET /communications/callRecords` using bounded `startDateTime` ranges.
2. Follow all pages.
3. Compare `(callRecordId, version)` with the ingestion ledger.
4. Enqueue missing records.
5. Enqueue records whose Graph version exceeds the stored version.

Recommended schedule:

* Daily: reconcile the preceding seven days.
* Weekly: reconcile the full rolling 30-day Graph window.
* After any detected outage or expired subscription: reconcile immediately.

The list endpoint does not expose a `lastModifiedDateTime` watermark, so overlapping windows are intentional.

## ADLS Gen2 design

Suggested folder layout:

```text
/staging/callrecords/ingest_date=YYYY-MM-DD/hour=HH/
/bronze/callrecords/ingest_date=YYYY-MM-DD/hour=HH/
/bronze/notifications/ingest_date=YYYY-MM-DD/hour=HH/
/control/manifests/
/control/ledger/
/quarantine/schema_or_parse_error/
```

Each raw object should preserve:

* Exact Graph JSON.
* Tenant ID.
* Subscription ID.
* Call-record ID.
* Graph version.
* Notification and ingestion timestamps.
* Graph request ID.
* Content hash.
* Parser version.
* Pipeline-run ID.

Compact small staging objects into approximately 64-256 MB gzip JSON or Parquet files before long-term retention.

## Fabric medallion model

### Bronze

Bronze preserves source fidelity:

* Raw Graph JSON.
* Notification envelopes.
* Manifests.
* Source request metadata.
* Every received call-record version.

Bronze should be immutable and tightly restricted.

### Silver

Normalize the Graph hierarchy into Delta tables such as:

| Table                  | Proposed grain                                |
| ---------------------- | --------------------------------------------- |
| `calls`                | One current row per tenant and call-record ID |
| `call_versions`        | One row per call-record version               |
| `participants`         | One row per participant in a call             |
| `sessions`             | One row per call session                      |
| `segments`             | One row per session segment                   |
| `media`                | One row per media instance                    |
| `streams`              | One row per media stream                      |
| `endpoints`            | One row per endpoint observation              |
| `network_observations` | One row per network observation               |
| `device_observations`  | One row per device observation                |
| `failures`             | One row per failure observation               |
| `user_feedback`        | One row per available feedback observation    |
| `processing_audit`     | One row per pipeline-processing event         |

Suggested current-record merge:

```sql
MERGE INTO silver.calls AS target
USING staged_calls AS source
ON target.tenant_id = source.tenant_id
AND target.call_record_id = source.call_record_id
WHEN MATCHED
  AND source.graph_version > target.graph_version
  THEN UPDATE SET *
WHEN NOT MATCHED
  THEN INSERT *
```

The implementation should also preserve a separate version-history table keyed by:

```text
tenant_id, call_record_id, graph_version
```

### Gold

Gold should expose approved, pseudonymized business measures:

* Daily and monthly call-quality trends.
* Packet-loss, jitter, and round-trip-time distributions.
* Audio and video quality indicators.
* Failure counts and rates.
* Device and client-version trends.
* Network, building, site, and subnet trends where approved enrichment exists.
* PSTN and Direct Routing operational trends.
* Reliability and completeness measures.
* Customer-approved quality classifications.

Gold can be published as:

* Lakehouse Delta tables for Direct Lake.
* Fabric Warehouse dimensions and facts.
* A governed Power BI semantic model.

## Candidate dimensional model

### Facts

* `FactCall`
* `FactSession`
* `FactMediaStream`
* `FactCallFailure`
* `FactUserFeedback`
* `FactPstnCall`
* `FactDirectRoutingCall`
* `FactIngestionCompleteness`

### Dimensions

* `DimDate`
* `DimTime`
* `DimTenant`
* `DimUserPseudonym`
* `DimDevice`
* `DimClient`
* `DimNetwork`
* `DimSite`
* `DimBuilding`
* `DimMediaType`
* `DimCallType`
* `DimFailure`
* `DimSbc`

This model must be finalized only after completing a CQD-to-Graph field mapping.

## CQD-to-Graph mapping exercise

Before production engineering, inventory every CQD dimension and measure used by the customer.

Use a mapping matrix:

| CQD field or measure      | Business purpose       | Graph source                        | Transformation                             | Coverage   | Decision  |
| ------------------------- | ---------------------- | ----------------------------------- | ------------------------------------------ | ---------- | --------- |
| Example: average jitter   | Audio quality trend    | `mediaStream.averageJitter`         | Unit normalization and aggregation         | To measure | Candidate |
| Example: packet-loss rate | Quality classification | `mediaStream.averagePacketLossRate` | Customer-defined threshold                 | To measure | Candidate |
| CQD building              | Site comparison        | No direct equivalent                | Customer-maintained subnet/site enrichment | Unknown    | Validate  |
| CQD Microsoft classifier  | Good/Poor quality      | Not guaranteed                      | Recreate only with approved specification  | Unknown    | Validate  |

Classify each item as:

* **Direct:** Available in Graph without material semantic change.
* **Derived:** Can be calculated from Graph fields.
* **Enriched:** Requires customer-supplied mapping, such as subnet-to-building.
* **Approximate:** Similar but not semantically identical.
* **Unavailable:** Cannot be reproduced from supported Graph data.

The proof of concept should proceed to production only after stakeholders approve this matrix.

## PSTN and Direct Routing

Graph provides separate functions:

```http
GET /communications/callRecords/getPstnCalls(
  fromDateTime=...,
  toDateTime=...
)

GET /communications/callRecords/getDirectRoutingCalls(
  fromDateTime=...,
  toDateTime=...
)
```

PSTN information can include:

* Calling user.
* Caller and callee numbers.
* Call type and duration.
* Cost and currency.
* Destination.
* Conference ID.
* Operator and license capability.

Direct Routing information can include:

* Direction and call type.
* Caller and callee numbers.
* Invite, start, end, and failure times.
* Final SIP code and phrase.
* Microsoft subreason.
* SBC FQDN.
* Media and signaling region.
* Correlation ID.
* Media-bypass status.

These datasets should be ingested separately and joined only through validated keys. A PSTN or Direct Routing log row can represent only part of a larger call or meeting.

## Graph Data Connect alternative

Microsoft Graph Data Connect is designed for high-scale Microsoft 365 extraction and can land data in ADLS Gen2 or Fabric OneLake. Its documented `TeamsCallRecords_v1` dataset includes call activity such as organizers, attendees, join/leave activity, communication identifiers, and timestamps.

It does not currently document the media-quality, device, network, or QoE fields needed to replace CQD. It may complement the architecture for activity analytics but is not a complete CQD replacement.

## Volume and capacity estimates

The following values are initial planning assumptions and must be replaced with measured results from a sample.

| Item                                   |              Calculation |                          Estimate |
| -------------------------------------- | -----------------------: | --------------------------------: |
| Top-level calls                        |          100,000 per day |             36.5 million per year |
| Average completed calls                | 100,000 / 86,400 seconds |                   1.16 per second |
| Estimated 10x peak                     |                1.16 x 10 |                   11.6 per second |
| Notifications at 1.5 versions per call |          150,000 per day |           1.74 per second average |
| Notification payload at 1 KB           |       150,000 KB per day |      Approximately 150 MB per day |
| Expanded raw record at 25 KB           |          100,000 x 25 KB |      Approximately 2.5 GB per day |
| Annual raw storage                     |             2.5 GB x 365 |    Approximately 0.91 TB per year |
| Compressed Silver at 5-8 KB per call   |         100,000 x 5-8 KB |  Approximately 0.5-0.8 GB per day |
| Annual compressed Silver               |     Daily estimate x 365 | Approximately 183-292 GB per year |

Add approximately 20-30% for:

* Delta logs.
* Version history.
* Operational metadata.
* Statistics and indexes.
* Quarantine and retry data.

The customer's 100,000 CQD rows may be at media-stream grain rather than call grain. A sample must measure:

* Calls per day.
* Sessions per call.
* Segments per session.
* Media instances per segment.
* Streams per media instance.
* Record-size percentiles.
* Number of versions per call.

The volume is modest for Azure messaging, ADLS, and Fabric. Fabric capacity will be driven more by transformation schedules, Spark startup frequency, Direct Lake refresh, query concurrency, semantic-model design, and report usage than by 100,000 rows alone.

For a proof of concept, begin with an F2 or F4 capacity if available, use hourly micro-batches rather than per-event Spark processing, and measure usage with the Fabric Capacity Metrics app.

## File and table optimization

At the projected volume:

* Avoid creating excessive small files.
* Compact raw files to approximately 64-256 MB.
* Start Silver tables without daily partitions unless measurements demonstrate a benefit.
* Preserve `call_start_date` and `ingest_date` as columns.
* Use Delta file compaction.
* Consider V-Order for read-heavy curated tables.
* Consider Z-order or clustering on common filters such as call date and pseudonymous site/user keys.
* Introduce physical partitions only when partition sizes consistently approach approximately 1 GB and benchmarks show improvement.

## Schema evolution

Graph payloads can evolve. Use the following controls:

* Bronze accepts and preserves every source field.
* Silver uses an explicit schema contract.
* Preserve unknown fields in an `unknown_fields_json` column or quarantine object.
* Route additive fields through catalog review before promotion.
* Fail only the affected batch for breaking type changes.
* Use controlled per-write schema evolution rather than global automatic merging.
* Include parser and schema versions in every Silver row.
* Send the Graph header `Prefer: include-unknown-enum-members`.
* Alert on unknown enums, unknown properties, and parse failures.

## Security and privacy

Call-quality data can contain personal and network-identifying information.

### Identity and secrets

* Use a dedicated application with only `CallRecords.Read.All`.
* Prefer managed identity for Functions-to-Service Bus, ADLS, Key Vault, and monitoring.
* Use a certificate or federated workload identity for Graph where supported.
* Store webhook `clientState` and credentials in Key Vault.
* Disable shared-key access where practical.
* Never log access tokens or complete Graph payloads.

### Network security

* Use private endpoints for ADLS `dfs` and `blob` endpoints.
* Use private endpoints for Service Bus and Key Vault.
* Restrict public network access.
* If using a webhook during the proof of concept, expose only the ingress route through APIM or an equivalent controlled endpoint.

### Data access

* Restrict identified Bronze data to a small operational group.
* Combine Azure RBAC with ADLS ACLs.
* Use a Fabric workspace identity for the ADLS shortcut.
* Give report consumers Viewer access rather than broad workspace contributor roles.
* Apply OneLake row-level and column-level security where appropriate.
* Apply Purview classifications and sensitivity labels.

### Pseudonymization

In Silver and Gold:

* Replace Entra object IDs with keyed HMAC identifiers.
* Exclude display names by default.
* Exclude meeting URLs, phone numbers, endpoint names, IP addresses, and free text unless explicitly approved.
* Keep identity mappings in a separately secured store.
* Prevent personal data from entering logs, error messages, or dead-letter metadata.

### Example retention policy

The following is an architectural example, not a Microsoft requirement:

| Data category                   | Example retention |
| ------------------------------- | ----------------: |
| Staging objects                 |          3-7 days |
| Identified operational Bronze   |        30-90 days |
| Approved restricted raw archive |    Up to 365 days |
| Pseudonymized Silver            |         13 months |
| Aggregated Gold                 |      24-36 months |
| Reversible identity mapping     |           28 days |

Final retention must be approved by customer privacy, security, legal, and records-management stakeholders. Implement deletion and tiering using ADLS lifecycle policies.

## Monitoring and supportability

Monitor:

* Subscription expiration and renewal failures.
* Reauthorization lifecycle events.
* Notification volume deviation from baseline.
* Graph `429` and `5xx` rates.
* `Retry-After` duration.
* Service Bus queue depth and oldest-message age.
* Dead-letter count.
* Function failure and latency.
* Graph-to-ledger reconciliation gaps.
* ADLS errors, latency, capacity, and ingress.
* Bronze-to-Silver freshness.
* Call and version counts.
* Schema-quarantine count.
* Fabric pipeline and notebook failures.
* Fabric capacity-unit consumption and throttling.
* Report refresh and query latency.

Recommended tools:

* Application Insights and OpenTelemetry for Functions.
* Azure Monitor for Service Bus, Functions, Key Vault, and Storage.
* Log Analytics for operational correlation.
* Fabric Monitor Hub.
* Fabric Capacity Metrics app.

## Proof-of-concept plan

### Phase 0: Source discovery

**Scope**

* Inventory customer CQD reports, dimensions, measures, filters, and calculations.
* Sample 500-1,000 calls.
* Retrieve corresponding Graph records.
* Measure payload sizes and hierarchy cardinality.
* Create the CQD-to-Graph mapping matrix.

**Acceptance criteria**

* Required fields are categorized as Direct, Derived, Enriched, Approximate, or Unavailable.
* Record-size and child-count distributions are measured.
* Personally identifiable fields are cataloged.
* Stakeholders approve which semantic differences are acceptable.

### Phase 1: Subscription and ingress

**Scope**

* Register an Entra application.
* Grant and consent `CallRecords.Read.All`.
* Validate direct webhook behavior.
* Configure Event Grid and Service Bus.
* Implement subscription creation and renewal.

**Acceptance criteria**

* Subscription validation succeeds.
* Notification acknowledgment p99 is under three seconds.
* Subscription always has more than 24 hours remaining.
* Invalid `clientState` is rejected.
* Duplicate notifications are safely accepted.

### Phase 2: Graph retrieval and ADLS

**Scope**

* Implement queue-triggered fetch workers.
* Handle all paging.
* Preserve raw JSON and manifests.
* Execute seven days of live ingestion.
* Run a synthetic 10x peak test.

**Acceptance criteria**

* Every message is processed or dead-lettered with a sanitized reason.
* Every `@odata.nextLink` is processed.
* Graph `429` responses honor `Retry-After`.
* No uncontrolled retry or scale-out storm occurs.
* Raw data and ledger writes are durable and traceable.

### Phase 3: Recovery and idempotency

**Scope**

* Implement the ingestion ledger.
* Implement daily and weekly reconciliation.
* Simulate duplicates, out-of-order updates, worker crashes, and expired subscriptions.

**Acceptance criteria**

* One current Silver row exists per tenant and call-record ID.
* The highest Graph version wins.
* Version history is preserved.
* Duplicate reprocessing does not change business counts.
* Reconciliation recovers every Graph-visible record within 24-48 hours.

### Phase 4: Fabric medallion implementation

**Scope**

* Create the OneLake shortcut.
* Build Bronze-to-Silver transformations.
* Produce representative Gold measures.
* Create a semantic model and sample Power BI report.

**Acceptance criteria**

* Bronze remains reproducible and immutable.
* Schema additions do not lose source data.
* Daily reconciliation completeness is at least 99.9% within 24 hours.
* Completeness reaches 100% before Graph's 30-day expiration.
* Three representative customer quality questions can be answered.

### Phase 5: Security and operations

**Scope**

* Implement RBAC, ACLs, managed identities, private endpoints, pseudonymization, and monitoring.
* Conduct privacy and access tests.

**Acceptance criteria**

* Unauthorized users cannot read Bronze or identified fields.
* No personal information appears in logs.
* Subscription-expiry, freshness, failure, throttling, and dead-letter alerts fire during tests.
* Customer governance teams approve retention and access.

### Phase 6: Scale and production decision

**Scope**

* Replay approximately 3 million records.
* Optionally generate a 36.5-million-record annual performance dataset.
* Test agreed report scenarios and concurrency.

**Acceptance criteria**

* No Fabric throttling occurs during the approved processing schedule.
* Target report queries meet the agreed p95 response time, such as less than five seconds.
* Measured capacity-unit and storage consumption support a production cost estimate.
* The CQD-to-Graph mapping has acceptable field coverage.

## Go/no-go criteria

### Proceed to production when

* Required quality fields are available or can be validly derived.
* Stakeholders accept that the solution is not an exact CQD export.
* The initial 30-day backfill is sufficient or another sanctioned historical source is provided.
* Daily reconciliation reaches the agreed completeness target.
* Security and privacy controls are approved.
* Fabric query and refresh performance meets the service-level objective.

### Stop or redesign when

* Exact CQD classifications or measures are mandatory and cannot be reproduced.
* The customer requires more than 30 days of historical Graph backfill without another supported source.
* Required tenant building or network enrichment cannot be recreated.
* Personally identifiable telemetry cannot be governed appropriately.
* The customer requires a supported unattended export of the CQD cube itself.

## Questions for Microsoft product engineering

1. Is an app-only supported CQD bulk/query/export API planned for ADLS Gen2 or Fabric?
2. Can Microsoft provide a sanctioned one-time export of the customer's existing 12-month CQD history?
3. Is a media-quality and QoE dataset planned for Microsoft Graph Data Connect?
4. What are the authoritative mappings between CQD dimensions/measures and Graph call-record fields?
5. Which CQD quality classifiers and calculations can customers validly reproduce?
6. Can tenant building/network enrichment be obtained through any supported app-only interface?
7. What completeness guarantees apply to high-volume tenants and delayed call-record versions?
8. What are the documented retention periods for the PSTN and Direct Routing call-log functions?
9. Is a Fabric-native replacement planned for the CQD connector's interactive authentication model?

## Immediate next actions

1. Export an inventory of the customer's current CQD Power BI fields, measures, filters, and report questions.
2. Select 500-1,000 representative recent calls covering meetings, peer-to-peer calls, PSTN, and Direct Routing.
3. Obtain approval for an Entra application with `CallRecords.Read.All`.
4. Build the CQD-to-Graph mapping matrix before provisioning the complete production architecture.
5. Implement a seven-day ingestion spike into a temporary ADLS account.
6. Transform three high-value quality measures in Fabric and compare their trends with CQD.
7. Use measured field coverage, payload sizes, and report performance for the production decision.

## Official references

### CQD

* [Data and reports in Call Quality Dashboard](https://learn.microsoft.com/en-us/microsoftteams/cqd-data-and-reports)
* [Install the Power BI Connector for CQD](https://learn.microsoft.com/en-us/microsoftteams/cqd-power-bi-connector)
* [Use Power BI to analyze CQD data](https://learn.microsoft.com/en-us/microsoftteams/cqd-power-bi-query-templates)
* [CQD dimensions and measures](https://learn.microsoft.com/en-us/microsoftteams/dimensions-and-measures-available-in-call-quality-dashboard)

### Microsoft Graph call records

* [Call records API overview](https://learn.microsoft.com/en-us/graph/api/resources/callrecords-api-overview?view=graph-rest-1.0)
* [Call Records API FAQ](https://learn.microsoft.com/en-us/graph/callrecords-api-faq)
* [List callRecords](https://learn.microsoft.com/en-us/graph/api/callrecords-cloudcommunications-list-callrecords?view=graph-rest-1.0)
* [Get a callRecord](https://learn.microsoft.com/en-us/graph/api/callrecords-callrecord-get?view=graph-rest-1.0)
* [callRecord resource](https://learn.microsoft.com/en-us/graph/api/resources/callrecords-callrecord?view=graph-rest-1.0)
* [Call-record change notifications](https://learn.microsoft.com/en-us/graph/changenotifications-for-callrecords)
* [Change notification lifecycle events](https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events)
* [Webhook delivery and validation](https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks)
* [Microsoft Graph paging](https://learn.microsoft.com/en-us/graph/paging)
* [Microsoft Graph throttling guidance](https://learn.microsoft.com/en-us/graph/throttling)
* [Microsoft Graph service-specific throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits#cloud-communications-service-limits)
* [mediaStream resource](https://learn.microsoft.com/en-us/graph/api/resources/callrecords-mediastream?view=graph-rest-1.0)
* [networkInfo resource](https://learn.microsoft.com/en-us/graph/api/resources/callrecords-networkinfo?view=graph-rest-1.0)
* [deviceInfo resource](https://learn.microsoft.com/en-us/graph/api/resources/callrecords-deviceinfo?view=graph-rest-1.0)
* [Get PSTN calls](https://learn.microsoft.com/en-us/graph/api/callrecords-callrecord-getpstncalls?view=graph-rest-1.0)
* [Get Direct Routing calls](https://learn.microsoft.com/en-us/graph/api/callrecords-callrecord-getdirectroutingcalls?view=graph-rest-1.0)

### Event ingestion and Azure services

* [Subscribe to Microsoft Graph events through Event Grid](https://learn.microsoft.com/en-us/azure/event-grid/subscribe-to-graph-api-events)
* [Use Service Bus as an Event Grid handler](https://learn.microsoft.com/en-us/azure/event-grid/handler-service-bus)
* [Event Grid delivery and retry](https://learn.microsoft.com/en-us/azure/event-grid/delivery-and-retry)
* [Service Bus duplicate detection](https://learn.microsoft.com/en-us/azure/service-bus-messaging/duplicate-detection)
* [Service Bus dead-letter queues](https://learn.microsoft.com/en-us/azure/service-bus-messaging/service-bus-dead-letter-queues)
* [Azure Functions Service Bus trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-service-bus-trigger)
* [Managed identities for Azure Functions connections](https://learn.microsoft.com/en-us/azure/azure-functions/manage-connections)
* [ADLS Gen2 overview](https://learn.microsoft.com/en-us/azure/storage/blobs/data-lake-storage-introduction)
* [ADLS Gen2 access-control model](https://learn.microsoft.com/en-us/azure/storage/blobs/data-lake-storage-access-control-model)

### Microsoft Fabric

* [Fabric medallion lakehouse architecture](https://learn.microsoft.com/en-us/fabric/onelake/onelake-medallion-lakehouse-architecture)
* [Create an ADLS Gen2 OneLake shortcut](https://learn.microsoft.com/en-us/fabric/onelake/create-adls-shortcut)
* [Fabric notebook activity](https://learn.microsoft.com/en-us/fabric/data-factory/notebook-activity)
* [Fabric ADLS Gen2 Copy activity](https://learn.microsoft.com/en-us/fabric/data-factory/connector-azure-data-lake-storage-gen2-copy-activity)
* [Fabric Lakehouse and Delta tables](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-and-delta-tables)
* [Fabric table compaction](https://learn.microsoft.com/en-us/fabric/data-engineering/table-compaction)
* [Fabric capacity licenses](https://learn.microsoft.com/en-us/fabric/enterprise/licenses)
* [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
* [Fabric capacity throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
* [OneLake security](https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security)

### Graph Data Connect

* [Microsoft Graph Data Connect datasets and sinks](https://learn.microsoft.com/en-us/graph/data-connect-datasets)
* [TeamsCallRecords\_v1 schema](https://github.com/microsoftgraph/dataconnect-solutions/blob/main/Datasets/data-connect-dataset-teamscallrecords1.md)
* [Microsoft 365 connector for Fabric Data Factory](https://learn.microsoft.com/en-us/fabric/data-factory/connector-microsoft-365-overview)
