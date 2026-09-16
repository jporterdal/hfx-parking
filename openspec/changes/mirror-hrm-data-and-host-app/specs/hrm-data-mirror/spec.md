## Purpose

Holds a local copy of the Halifax Regional Municipality layers the product reads — service requests, custom fields and census dissemination areas — so that analysis over any violation type is a query against data already held rather than a pass over the network, and so that the cost of tracking thirty violation types is a grouping operation rather than thirty times the network load.

A mirror can be stale in a way a live query cannot, so this capability is responsible for making its own freshness observable. Every requirement below that concerns provenance exists because a silently stalled sync produces an application that looks healthy while serving old data.

## ADDED Requirements

### Requirement: Hold a local copy of the source layers

The system SHALL maintain a local store of the HRM Cityworks service requests, the parking-relevant rows of the Cityworks custom-fields layer, and the census dissemination areas, sufficient for every downstream capability to run without querying the source.

Downstream analysis SHALL read only from this store. No analysis path may fall back to a live query against the source, because a partial fallback would make it impossible to tell whether a result came from mirrored or live data.

#### Scenario: Analysis runs with the source unreachable

- **WHEN** the derivation runs and the HRM service is unavailable
- **THEN** it completes against the mirror and its output states the mirror's freshness

#### Scenario: No live fallback

- **WHEN** a requested record is absent from the mirror
- **THEN** the system reports it as absent rather than fetching it from the source

### Requirement: Load the mirror by offset paging

The initial load SHALL retrieve rows by paging on offset rather than by requesting identifier chunks.

The distinction is measured, not stylistic: identifier-chunk queries against these layers cost roughly six seconds per chunk, while offset paging the same layers returns 1,000 rows in about half a second. The full corpus is roughly 1,250 pages.

#### Scenario: Full load completes

- **WHEN** an initial load runs against an empty store
- **THEN** every page of each source layer is retrieved to exhaustion and the stored row count matches the service's own count query for that layer

#### Scenario: Load is resumable at page granularity

- **WHEN** an initial load fails partway
- **THEN** a subsequent load continues from the last completed page rather than restarting

### Requirement: Sync incrementally by monotonic identifier

Neither Cityworks layer publishes a last-modified field, so incremental sync SHALL advance a per-layer high-water mark on `ObjectId`, retrieving rows whose identifier exceeds the mark.

The system SHALL verify that the identifier is monotonic with respect to recency at sync time rather than assuming it, and SHALL report when that assumption fails.

#### Scenario: New rows are captured

- **WHEN** a sync runs after rows have been added to the source
- **THEN** those rows are stored and the watermark advances to the highest identifier retrieved

#### Scenario: No new rows

- **WHEN** a sync runs and the source has no rows beyond the watermark
- **THEN** the sync is recorded as successful, the watermark is unchanged, and no error is raised

#### Scenario: Monotonicity assumption is violated

- **WHEN** a retrieved row carries an identifier above the watermark but an initiation date older than rows already held
- **THEN** the system reports the anomaly rather than silently accepting it

### Requirement: Refetch the mutable request set on every sync

A high-water mark captures inserts and misses edits. Requests are edited in place after filing: a request closes, and its closure date, status and resolution change without its identifier moving. The tow flag on its custom-field rows can likewise be set after the call is filed.

The system SHALL therefore refetch, on every sync, the full set of requests the mirror holds as open, together with their custom-field rows, and update them. The set is bounded and small — 3,351 requests were open when this was specified — so refetching it entirely costs a handful of pages.

A request the mirror holds as open which the source no longer reports as open SHALL be updated to its current state.

#### Scenario: A request closes after it was mirrored

- **WHEN** a request held as open has since been closed at the source
- **THEN** the next sync updates its closure date, status and resolution in the mirror

#### Scenario: A tow flag is set after filing

- **WHEN** the tow custom-field value for a request held as open changes at the source
- **THEN** the next sync reflects the new value

#### Scenario: Edits to long-closed records

- **WHEN** a record the mirror holds as closed is edited at the source
- **THEN** the system does not claim to have captured it, and a periodic full reload is the stated remedy

### Requirement: Pace the sync by the source's own update clock

The system SHALL poll each mirrored layer's published last-edit timestamp and pull when it advances, rather than pulling on a fixed interval chosen independently of the source.

The poll SHALL run at least daily. The pull SHALL run only when the poll reports the timestamp advanced.

These are two cadences and SHALL NOT be collapsed into one. The poll is one metadata request per layer with no paging, cheap enough to run nightly without meaningfully loading the service, and running it nightly bounds how far behind the source the mirror can fall to one day. The pull is the expensive half and has no reason to run on a night when the source has published nothing.

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

#### Scenario: Poll accelerates but does not gate

- **WHEN** the last-edit timestamp does not advance despite rows having changed
- **THEN** the watermark pull and open-set refetch still run on their own schedule

### Requirement: Run unattended

The sync SHALL run with no credentials and no human involvement, and SHALL record its outcome.

#### Scenario: Scheduled sync runs unattended

- **WHEN** the schedule fires
- **THEN** the sync runs without credentials and records its outcome

### Requirement: Record the outcome of every sync attempt

The system SHALL record, for each sync attempt: when it started, whether it succeeded, the watermark it reached, the number of rows inserted and updated per layer, the source's last-edit timestamp as observed, and, on failure, the error.

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

The system SHALL compare, on each sync, the row count it holds per layer against the source's own count query for that layer, and SHALL report a divergence.

A mirror that has drifted from its source is worse than no mirror, because it carries the authority of a complete copy. The check is one request per layer.

#### Scenario: Counts agree

- **WHEN** a sync completes and stored counts match the service's counts
- **THEN** the sync is recorded as reconciled

#### Scenario: Counts diverge

- **WHEN** stored counts differ from the service's counts
- **THEN** the divergence is reported with both figures rather than being resolved silently

### Requirement: Support a full reload

The system SHALL support discarding and rebuilding the mirror from the source.

This is the stated remedy for every failure mode the incremental rule cannot cover — in-place edits to long-closed records, a violated monotonicity assumption, an unexplained count divergence. It is viable as a remedy because it is cheap: the full corpus is roughly 1,250 pages at about half a second each.

#### Scenario: Full reload restores agreement

- **WHEN** a full reload runs after a reported divergence
- **THEN** the rebuilt mirror's counts match the service's counts
