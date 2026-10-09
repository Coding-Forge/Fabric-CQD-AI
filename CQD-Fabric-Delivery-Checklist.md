# Teams Call-Quality Analytics Delivery Checklist

**Status:** Planning; no deliverables accepted yet

**Architecture reference:** [CQD, Microsoft Graph, ADLS Gen2, and Microsoft Fabric Solution Exploration](CQD-Graph-Fabric-Solution-Exploration.md)

**Build preparation:** Python Azure Functions and Fabric notebooks were selected for an Azure Government POC in `<resource-group>`, `usgovvirginia`, with Microsoft 365 GCC High as the source. Only code and infrastructure preparation is authorized; deployment is not. The requested Fabric destination is `<fabric-capacity>` in `<fabric-resource-group>`; its cloud, capacity type, workspace, and connectivity remain unverified. See [implementation status and deployment gates](README.md).

**Source-access spike:** With no CQD report currently available, read-only Graph access and a small local raw sample are authorized as an early technical spike. This does not accept reporting coverage or authorize deployment/permission changes. The user supplied evidence of application permission and administrator consent and demonstrated live listing with an empty window on October 7, 2026. Expanded record retrieval remains unverified; intermittent HTTP 400 responses require request-stage diagnostics. A replacement credential must be supplied locally, never in chat.

## Objective and tracking

Deliver durable, performant Teams call-quality analytics through ADLS Gen2, Microsoft Fabric, and Power BI without losing required customer reporting outcomes.

Microsoft Graph call records are a candidate source, not an exact CQD export. Source coverage and acceptable semantic differences must be established before committing to the production architecture.

Work through the numbered deliverables in order. Check an item only when its output exists and its acceptance criteria have been demonstrated. Record the evidence, location, and approval beside the item or in the deliverable's completion record. A phase is complete only when all its required items are accepted; document exclusions explicitly rather than checking unfinished work.

## 1. Confirm customer requirements and baseline

**Deliverable:** Approved reporting requirements and current-state baseline.

- [ ] Inventory existing CQD Power BI reports, fields, measures, filters, calculations, and quality classifications.
- [ ] Identify the business questions that must remain answerable; select three representative questions for the proof of concept.
- [ ] Separate mandatory requirements from desirable capabilities, including PSTN, Direct Routing, site/building reporting, and user-level troubleshooting.
- [ ] Confirm historical backfill needs, future retention, freshness, report concurrency, and privacy/access requirements.
- [ ] Measure current report/query performance and determine what the reported 100,000 daily rows represent: calls, sessions, streams, or aggregates.
- [ ] Agree on measurable acceptance targets for field coverage, completeness, freshness, report latency, and cost.

**Exit gate:** The customer approves the requirements and baseline. Performance and completeness targets have defined measurement methods and denominators.

## 2. Validate supported source access and obtain sample data

**Deliverable:** Source capability findings and a secured, representative sample.

- [ ] Verify current Microsoft documentation for Graph field availability, API behavior, retention, permissions, pagination, notifications, and service limits.
- [ ] Confirm whether any sanctioned source can supply required CQD-only fields or historical data beyond Graph's retention window.
- [ ] Obtain approval for a test identity with the required Graph application permissions and administrator consent.
- [ ] Approve sample access, secure storage, handling, and deletion before retrieving identified telemetry.
- [ ] Select 500-1,000 representative recent calls, including required meeting, peer-to-peer, PSTN, and Direct Routing scenarios.
- [ ] Retrieve corresponding Graph records and available CQD comparison results; document matching keys and any unmatched samples.
- [ ] Measure calls per day, hierarchy cardinalities, payload-size distributions, versions per call, field population, and identifiable data.

**Exit gate:** Supported access works, sample coverage is documented, and source limitations and historical constraints are explicit.

## 3. Complete CQD-to-Graph mapping and feasibility decision

**Deliverable:** Approved field/measure mapping and a documented go, conditional-go, or no-go decision.

- [ ] Map every mandatory CQD field and measure to its business purpose and candidate Graph source.
- [ ] Classify each mapping as Direct, Derived, Enriched, Approximate, or Unavailable.
- [ ] Specify units, aggregation grain, null handling, calculation rules, and required enrichment for mapped measures.
- [ ] Compare sample results, including quality classifications, and explain discrepancies rather than assuming parity.
- [ ] Document unsupported requirements, historical gaps, acceptable substitutions, and any required scope changes.
- [ ] Obtain stakeholder approval of semantic differences and the feasibility decision.

**Exit gate:** Proceed only if mandatory outcomes are achievable or explicit scope changes are approved. If not, stop or redesign; do not build the production platform to compensate for unavailable source data.

## 4. Agree on the proof-of-concept design

**Deliverable:** Approved, bounded technical-spike design and measurement plan.

- [ ] Confirm the minimum POC resources, Azure/Fabric environments, ownership, budget, and deployment method.
- [ ] Choose POC ingress and the intended production path; distinguish direct-webhook validation from Event Grid delivery requirements.
- [ ] Define raw storage, ingestion ledger, manifests, record/version keys, and retry/dead-letter behavior.
- [ ] Define initial backfill, subscription renewal, reconciliation, and outage-recovery procedures within verified source retention.
- [ ] Define the proposed Silver grains and relationships, approved Gold measures, and Power BI consumption mode. Working decision (October 8, 2026, pending customer approval): keep the ADLS Bronze ingestion; build Delta Silver/Gold tables in a Fabric Lakehouse; consume them from Power BI through Direct Lake. The user reports Real-Time Intelligence and Direct Lake are available in the target environment (not independently verified). An Eventhouse is optional later, fed from Bronze or Delta for ad-hoc KQL; Eventstream is not planned because Graph call records are pulled, not streamed. At 100K+ records per day, load-test Graph throttling before sizing.
- [ ] Confirm required enrichment sources and privacy controls, including pseudonymization, access, and retention.
- [ ] Define test cases and evidence collection against the agreed acceptance targets.

**Exit gate:** The POC has an approved scope and design. Production infrastructure choices remain provisional until measured.

## 5. Implement and prove reliable ingestion

**Deliverable:** Working Graph-to-ADLS ingestion with operational evidence.

- [ ] Provision approved POC identities, secure storage, messaging, and monitoring.
- [ ] Implement subscription creation, renewal, lifecycle handling, and ingress validation.
- [ ] Implement durable queueing and fetch workers with bounded concurrency, full pagination, and Graph throttling/retry handling.
- [ ] Persist source JSON, source metadata, manifests, and a durable ingestion ledger before acknowledging successful processing.
- [ ] Implement idempotency and version handling; preserve observed versions without allowing older records to replace newer ones.
- [ ] Implement bounded historical backfill and overlapping reconciliation windows for missing records and newer versions.
- [ ] Run seven days of live ingestion and measure latency, volume, completeness, payload sizes, and failures.
- [ ] Exercise duplicates, out-of-order updates, worker crashes, expired subscriptions, throttling, dead letters, and recovery.
- [ ] Run an agreed synthetic peak-load test without exceeding source service limits.

**Exit gate:** Ingestion meets agreed reliability and freshness targets. Every queued message is accounted for, recovery is demonstrated, and gaps are surfaced explicitly.

## 6. Build Fabric tables and representative reporting

**Deliverable:** Reproducible Bronze-to-Silver-to-Gold processing and a sample Power BI report.

- [ ] Connect Fabric to approved ADLS data and verify source-access boundaries.
- [ ] Implement explicit-schema normalization into the required Silver tables.
- [ ] Apply version-aware updates consistently across parent and child tables, preserving history and removing stale current-version children.
- [ ] Implement schema-change handling, quarantine, processing audit, and replay from raw data.
- [ ] Apply approved pseudonymization and enrichment without exposing restricted source fields.
- [ ] Produce the three agreed business measures and document their definitions and differences from CQD.
- [ ] Build the semantic model and report with correct relationships, aggregation grain, and approved access controls.
- [ ] Compare report outcomes with CQD and validate counts, units, nulls, joins, and quality classifications.

**Exit gate:** The report answers the agreed business questions, approved semantic differences are visible, and reprocessing does not alter counts incorrectly.

## 7. Validate scale, cost, governance, and production suitability

**Deliverable:** Measured POC assessment and production go/no-go recommendation.

- [ ] Measure end-to-end completeness against Graph-visible records and separately document any unmeasurable source-level completeness.
- [ ] Test freshness, report p95 latency, and concurrency against customer-approved targets.
- [ ] Replay an agreed representative workload; use larger annual-volume tests only where needed to resolve capacity uncertainty.
- [ ] Measure Fabric capacity use, throttling, storage growth, request volume, and Azure operating costs.
- [ ] Tune batching, file sizes, compaction, and table layout using measured results.
- [ ] Validate access isolation, pseudonymization, retention/deletion, and sanitized logs and dead-letter metadata.
- [ ] Demonstrate subscription, freshness, ingestion, schema, reconciliation, and capacity alerts.
- [ ] Obtain customer acceptance of reporting coverage, operational risk, governance, and the production cost estimate.

**Exit gate:** Production proceeds only with approved evidence that required outcomes and service targets are achievable. Unresolved blockers result in redesign or no-go.

## 8. Productionize and transition reporting

**Deliverable:** Supported production service and an approved reporting transition.

- [ ] Finalize and deploy production infrastructure, identities, network controls, and CI/CD.
- [ ] Assign operational owners and publish runbooks for renewal, reconciliation, replay, dead letters, schema changes, and incidents.
- [ ] Implement approved retention, deletion, recovery, and configuration-management procedures.
- [ ] Perform the supported initial backfill and document any historical period that remains unavailable.
- [ ] Run CQD and the new reports in parallel for an agreed period and resolve material discrepancies.
- [ ] Confirm production acceptance, support readiness, monitoring, and rollback criteria.
- [ ] Transition approved reports and consumers; retain CQD for any requirements the new platform does not cover.

**Exit gate:** The production service has named owners, accepted reporting outcomes, and demonstrated recovery. Existing CQD reporting is retired only where its replacement has been approved.

## Completion and decision record

Add entries as deliverables are accepted or decisions change.

### Build preparation progress

These items track preparation only; they do not accept the numbered customer or deployment gates.

- [x] Preserve the exploration and delivery checklist in local Git (initial commit `d0c6693`).
- [x] Confirm Python Azure Functions and Fabric notebooks as the selected stack.
- [x] Confirm Azure Government / GCC High targets and preparation-only authorization.
- [x] Prepare disabled-by-default reconciliation, Service Bus retrieval, raw storage, and infrastructure source.
- [x] Complete local code and infrastructure validation and preserve the implementation checkpoint in Git.
- [x] Prepare and locally validate a bounded read-only API probe with protected, Git-ignored sample output.
- [ ] Demonstrate live read-only Graph access and retrieve a small secured sample using the local probe.
- [ ] Obtain deployment authorization and complete the README deployment gates.
- [ ] Confirm a remote destination and authorization to push.

| Deliverable / decision | Status | Evidence / artifact location | Approver / owner | Date |
| ---------------------- | ------ | ---------------------------- | ---------------- | ---- |
| Initial plan | Draft; awaiting approval | This checklist | Not assigned | 2026-10-07 |
| Ingestion foundation | Prepared; not deployed or accepted for live data | `function_app.py`, `cqd/`, `infra/main.bicep`; local validation evidence in README | Customer acceptance pending | 2026-10-07 |
