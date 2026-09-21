# hrm-data-mirror Specification

## Purpose

Holds a local copy of the Halifax Regional Municipality layers the product reads — service requests, custom fields and census dissemination areas — so that analysis over any violation type is a query against data already held rather than a pass over the network, and so that the cost of tracking thirty violation types is a grouping operation rather than thirty times the network load.

A mirror can be stale in a way a live query cannot, so this capability is responsible for making its own freshness observable. Every requirement below that concerns provenance exists because a silently stalled sync produces an application that looks healthy while serving old data.

## Requirements

### Requirement: Hold a local copy of the source layers

The system SHALL maintain a local store of the HRM Cityworks service requests, the Cityworks custom-fields layer **in full**, and the census dissemination areas, sufficient for every downstream capability to run without querying the source.

Downstream analysis SHALL read only from this store. No analysis path may fall back to a live query against the source, because a partial fallback would make it impossible to tell whether a result came from mirrored or live data.

#### Scenario: Analysis runs with the source unreachable

- **WHEN** the derivation runs and the HRM service is unavailable
- **THEN** it completes against the mirror and its output states the mirror's freshness

#### Scenario: No live fallback

- **WHEN** a requested record is absent from the mirror
- **THEN** the system reports it as absent rather than fetching it from the source

### Requirement: Load the mirror by offset paging

The initial load SHALL retrieve rows by paging on offset rather than by requesting identifier chunks.

The distinction is measured, not stylistic: identifier-chunk queries against these layers cost roughly six seconds per chunk, while offset paging the same layers returns 1,000 rows in about a second. The full corpus is 1,640 pages — 1,157 custom fields, 476 requests, 4 census.

#### Scenario: Full load completes

- **WHEN** an initial load runs against an empty store
- **THEN** every page of each source layer is retrieved to exhaustion and the stored row count matches the service's own count query for that layer

#### Scenario: Load is resumable at page granularity

- **WHEN** an initial load fails partway
- **THEN** a subsequent load continues from the last completed page rather than restarting

### Requirement: Replace a layer wholly when its published version advances

The source layers are published snapshots, not append logs: the service declares `hasStaticData` true, offers only `Query` and `Extract`, and reports the same edit instant for data and schema. Their `ObjectId` is system-maintained by the service and assigned afresh on each publish — the stored id space is dense from 1 to N with no gaps, which is a row counter written at load time, not a durable record identity.

The system SHALL reload a layer in full when its published version advances, and SHALL NOT attempt to retrieve only the rows that changed within a version.

The system SHALL NOT depend on any property of `ObjectId` surviving a publish — not its ordering, not its stability for a given record, and not its relationship to any date.

#### Scenario: A published version advances

- **WHEN** a layer's published edit timestamp is later than the version the mirror holds
- **THEN** that layer is reloaded in full and its stored count is reconciled against the service's own count

#### Scenario: Edits within a version need no special handling

- **WHEN** a request the mirror held as open has since closed, or a tow flag has been set after filing
- **THEN** the reload reflects the current state, because every row is retrieved rather than a subset

#### Scenario: Identifiers are not trusted across versions

- **WHEN** a reload assigns different identifiers to records the mirror already held
- **THEN** the mirror is correct regardless, because the previous version is replaced rather than merged into

### Requirement: Retain each version's identity so the sync method can be re-evaluated

Reloading in full is correct under every hypothesis about identifier stability, which is why it is adopted before that stability is known. It is not necessarily the cheapest method, and whether an incremental sync is possible SHALL remain an open question rather than be treated as settled.

No cross-publish observation of identifiers exists: the mirror was loaded from a single published version. Committed output either side of an earlier publish shows records persisting, but carries no identifiers, so it cannot distinguish a preserving reload from a renumbering one.

The system SHALL record, on each reload and before the previous version is replaced, that version's published edit timestamp, its row count, its highest identifier, and a sample of business-key to identifier mappings sufficient to detect renumbering.

A sample rather than the complete mapping, because a few thousand observations settle a yes-or-no question that the full set would answer at needless cost.

#### Scenario: Version identity is retained

- **WHEN** a reload replaces a layer
- **THEN** the replaced version's edit timestamp, row count, highest identifier and key-to-identifier sample remain queryable afterwards

#### Scenario: Renumbering is detectable across versions

- **WHEN** two or more versions have been retained
- **THEN** whether a given business key kept its identifier between them can be determined from the retained samples

### Requirement: Pace the sync by the source's own update clock

The system SHALL poll each mirrored layer's published last-edit timestamp and pull when it advances, rather than pulling on a fixed interval chosen independently of the source.

The poll SHALL run at least daily. The pull SHALL run only when the poll reports the timestamp advanced.

These are two cadences and SHALL NOT be collapsed into one. The poll is one metadata request per layer with no paging, cheap enough to run nightly without meaningfully loading the service, and running it nightly bounds how far behind the source the mirror can fall to one day. The pull is the expensive half and has no reason to run on a night when the source has published nothing.

The poll therefore gates every pull; nothing pulls on its own schedule. This rests on the layers being published snapshots — they declare static data and offer only `Query` and `Extract` — which is inferred from service metadata, not observed from HRM's publishing practice. An edit that did not advance the timestamp would go unseen. That risk is accepted for now and is subject to revision once HRM's actual update method is better understood.

No published HRM refresh schedule is known. The system SHALL therefore record the source's last-edit timestamp with every sync so that the interval between source updates accumulates as measurement, and SHALL derive any statement about when the next source update is expected from that observed history. Where insufficient history exists, the system SHALL report the next source update as not yet known rather than estimating one.

A layer that does not change — the census dissemination areas have not been edited since 2024-03-19 — SHALL NOT be re-pulled merely because a schedule fired.

#### Scenario: Source has not changed

- **WHEN** a poll finds the source's last-edit timestamp unchanged since the last sync
- **THEN** no pull is performed and the attempt is recorded as successful

#### Scenario: The mirror is never more than a day behind the source

- **WHEN** the source publishes on any given day
- **THEN** the following night's poll detects it and a pull runs

#### Scenario: Source has changed

- **WHEN** a poll finds the last-edit timestamp advanced
- **THEN** a pull runs and records the source timestamp it observed

#### Scenario: Cadence is measured, not assumed

- **WHEN** a consumer asks when the next source update is expected
- **THEN** the answer is derived from observed intervals, or reported as not yet known where too few have been observed

#### Scenario: Static reference layer

- **WHEN** a layer's last-edit timestamp has not advanced in months
- **THEN** it is not re-pulled

#### Scenario: Poll gates every pull, an accepted risk

- **WHEN** rows change at the source without the layer's last-edit timestamp advancing
- **THEN** no pull runs and the mirror keeps the version it holds, and this gap is recorded as an accepted risk rather than covered by a backstop
- **AND** the acceptance is revisited if research into how HRM updates its source shows edits that do not advance the timestamp, at which point a pull independent of the poll becomes required

### Requirement: Run unattended

The sync SHALL run with no source credentials and no human involvement, and SHALL record its outcome.

#### Scenario: Scheduled sync runs unattended

- **WHEN** the schedule fires
- **THEN** the sync runs without source credentials and records its outcome

### Requirement: Record the outcome of every sync attempt

The system SHALL record, for each sync attempt: when it started, whether it succeeded, the highest `ObjectId` it loaded (kept as evidence for design M2a, not used as an input to any later sync), the number of rows inserted and updated per layer, the source's last-edit timestamp as observed, and, on failure, the error.

A failed sync SHALL be recorded as a row, not as an absence of one. The system SHALL distinguish the last sync *attempt* from the last *successful* sync, because a sync failing repeatedly has a recent attempt and is exactly as stale as one that stopped entirely.

#### Scenario: A failed sync is recorded

- **WHEN** a sync fails
- **THEN** the attempt is recorded with its error, and the last successful sync time is unchanged

#### Scenario: Attempt and success are separately available

- **WHEN** a consumer asks how fresh the mirror is
- **THEN** it can obtain both the last attempt time and the last successful sync time

### Requirement: Publish four freshness clocks to consumers

The system SHALL expose, separately: the source's last-edit timestamp as last observed, the most recent call date the mirror holds, the last successful sync time, and when the next sync is due.

These SHALL NOT be conflated. They answer different questions, and the difference between them is what tells a reader whether a lag belongs to HRM or to this system. The source clock says when HRM last published. The data clock says how current what HRM published is. The sync clock says whether this system is still collecting. The due clock says whether it is behind.

The system SHALL additionally expose whether a scheduled sync is overdue — the scheduled time having passed by a grace period with no successful sync.

#### Scenario: Four clocks are separately available

- **WHEN** a consumer requests mirror freshness
- **THEN** it receives the source last-edit time, the most recent call date, the last successful sync time and the next due time, each distinguishable from the others

#### Scenario: The lag belongs to the source

- **WHEN** syncs are succeeding but HRM has not published since some days ago
- **THEN** the source clock shows the lag and the sync clock is not reported as the data's currency

#### Scenario: The lag belongs to this system

- **WHEN** HRM has published more recently than the last successful sync
- **THEN** the mirror reports itself behind the source

#### Scenario: A scheduled sync did not happen

- **WHEN** the next due time has passed by the grace period with no successful sync
- **THEN** the mirror reports the sync as overdue rather than reporting a due time that has passed

### Requirement: Retain the lists each sync produced

The system SHALL retain, per sync, the doorway and block lists derived under the default parameters, so that which doorways were listed and when accumulates as a record.

This replaces a record that existed by accident. Generated output was previously committed to the repository by a scheduled job, whose git history was the only before-and-after data this project has ever held — the raw material for asking whether an installed remedy changed anything. Removing the scheduled job removes that mechanism, so the record moves into the store deliberately rather than lapsing unnoticed.

Retention SHALL be queryable alongside the calls that produced it, and SHALL NOT require reading version control.

#### Scenario: A doorway leaves the list

- **WHEN** a doorway listed in one sync is absent from a later one
- **THEN** both states are retained and the sync at which it left can be identified

#### Scenario: History outlives the repository

- **WHEN** the list history is queried
- **THEN** it is available from the store without consulting version control

### Requirement: Reconcile the mirror against the source

The system SHALL compare, on each sync, the row count it holds per layer against the source's own count query for that layer, and SHALL report a divergence. The system SHALL also provide a re-runnable row-level reconciliation of a violation type's selection against the service, reporting ids, rows and values that differ.

A mirror that has drifted from its source is worse than no mirror, because it carries the authority of a complete copy. The count check is one request per layer. Equal counts do not prove equal rows, which is why the row-level check exists and why nothing depends on the mirror until it has passed.

#### Scenario: Counts agree

- **WHEN** a sync completes and stored counts match the service's counts
- **THEN** the sync is recorded as reconciled

#### Scenario: Counts diverge

- **WHEN** stored counts differ from the service's counts
- **THEN** the divergence is reported with both figures rather than being resolved silently

#### Scenario: Rows are identical for a selection

- **WHEN** a violation type's selection is reconciled at row level against the service, with the mirror holding the service's current published version
- **THEN** the mirror and the service hold the same request ids, the same service-request field values and the same custom-field rows and values for that selection, and any id, row or value present on one side only is reported

#### Scenario: Row-level reconciliation against a newer publish

- **WHEN** the service has published a version newer than the one the mirror holds
- **THEN** the reconciliation reports the version difference before any row difference, and does not reload or top up the mirror itself

### Requirement: Support a full reload

The system SHALL support discarding and rebuilding the mirror from the source.

A full reload is the primary sync path, not a fallback: it is what happens whenever a published version advances. It SHALL also be available on demand, for an unexplained count divergence or any other reason to distrust what is held.

It is viable as the primary path because it is cheap at this cadence: the full corpus is 1,640 pages, observed at 25 minutes 10 seconds and roughly 213 MB, against a source that publishes on the order of weekly.

#### Scenario: Full reload restores agreement

- **WHEN** a full reload runs after a reported divergence
- **THEN** the rebuilt mirror's counts match the service's counts
