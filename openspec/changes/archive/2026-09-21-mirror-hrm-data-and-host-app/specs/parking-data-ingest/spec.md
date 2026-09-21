## Purpose

Unchanged: joins the HRM datasets that hold a parking complaint, its outcome, and the neighbourhood it sits in, and normalizes the result so every downstream consumer reads one clean set of calls.

What changes is where the rows come from. Selection, joining, address reduction, doorway location and census placement now run against the local mirror rather than issuing queries to HRM on every run. The network fetch and the paging, retry and provenance concerns that came with it move into `hrm-data-mirror`.

## MODIFIED Requirements

### Requirement: Select calls by alleged violation

The violation type lives in the custom-fields data, not on the call record, so selection starts there: the system SHALL find the request identifiers whose alleged violation matches a caller-supplied label, then retrieve those requests **from the mirror**.

Matching SHALL be by substring, case-insensitively, because HRM records one problem under more than one label. A blocked driveway is filed as both `Blocking Driveway (DISPATCH)` and `DRIVEWAY`; a rule that only strips the `(DISPATCH)` suffix would leave the two as separate types and undercount the problem.

The system SHALL report which labels the match resolved to, so an operator can see what was actually included.

Because selection now runs against held data rather than the network, its cost no longer scales with the number of violation types being processed. Selecting every tracked type SHALL NOT require a pass over the source per type.

#### Scenario: Multiple labels for one problem

- **WHEN** calls are selected for the violation substring `Driveway`
- **THEN** both `Blocking Driveway (DISPATCH)` and `DRIVEWAY` are included, and their combined total is reported

#### Scenario: Matched labels are disclosed

- **WHEN** a selection runs
- **THEN** the distinct alleged-violation labels it matched are reported before the results are used

#### Scenario: Violation is caller-supplied

- **WHEN** a different violation substring is supplied
- **THEN** the same derivation runs unchanged against that violation

#### Scenario: Selection issues no source query

- **WHEN** calls are selected for any violation type
- **THEN** no request is made to the HRM service

### Requirement: Stamp every run with its provenance

Every output SHALL carry: when the derivation ran, when the mirror last synced successfully, and the most recent call date the mirror holds.

Three clocks rather than one, and they answer different questions. The derivation time says when these figures were computed. The sync time says whether data is still arriving. The most recent call date says whether the source itself is still moving. A mirrored architecture can fail in ways that leave any one of the three looking healthy while another has stopped, and a reader cannot tell a stalled pipeline from a stalled source without seeing them separately.

Where the mirror reports itself stale, every output SHALL say so.

#### Scenario: Run timestamp available to outputs

- **WHEN** a run writes its outputs
- **THEN** each output carries the time the run executed and the date of the most recent call

#### Scenario: Window anchor stated

- **WHEN** a recency window is applied
- **THEN** the date it is anchored to is stated with the results

#### Scenario: Stalled pipeline is visible

- **WHEN** the source stops updating and a derivation produces the same most-recent-call date as the previous one
- **THEN** a reader can tell from the outputs that the data has not advanced

#### Scenario: Three clocks travel together

- **WHEN** any output is produced
- **THEN** it carries the derivation time, the last successful sync time and the most recent call date

#### Scenario: Stale mirror is disclosed in output

- **WHEN** the derivation runs against a mirror reporting itself stale
- **THEN** every output it produces states that the data may be out of date

#### Scenario: An old output is identifiable as old

- **WHEN** a reader opens an output produced some time ago
- **THEN** they can tell how old it is without consulting version history

### Requirement: Place each doorway in a census neighbourhood

Each located doorway SHALL be assigned to the census dissemination area containing it, reading the polygons from the mirror.

Containment SHALL be determined correctly for polygons with interior holes, and the result SHALL agree with the authoritative spatial answer for the same point. Whether that is computed by a spatial index in the store or by the existing bounding-box prefilter and even-odd ray cast is an implementation choice; the agreement is the requirement.

The system SHALL report how many doorways fell in no census block.

#### Scenario: Doorway assigned to its neighbourhood

- **WHEN** a located doorway falls inside a census polygon
- **THEN** it carries that polygon's identifier and dwelling count, matching the authoritative spatial answer for that point

#### Scenario: Interior hole excluded

- **WHEN** a doorway falls inside a hole within a polygon
- **THEN** it is not assigned to that polygon

#### Scenario: Unplaced doorways reported

- **WHEN** a derivation completes
- **THEN** the number of doorways that fell in no neighbourhood is reported

## ADDED Requirements

### Requirement: Derive every tracked violation type from one pass over the mirror

The system SHALL produce the doorway list, block list and effectiveness evidence for every tracked canonical violation type from the data already held, without a network pass per type.

Types SHALL remain independent: no doorway, block or figure from one type may appear in another type's output. Cheap computation widens coverage; it does not merge the types, and it does not license a type to inherit a conclusion measured on a different one.

#### Scenario: All types derived together

- **WHEN** the derivation runs for every tracked canonical type
- **THEN** each type produces its own doorway list, block list and effectiveness evidence, and no source query is issued

#### Scenario: Types stay separate

- **WHEN** outputs for two tracked types are compared
- **THEN** no doorway or block appears in both as though it were the same finding

## REMOVED Requirements

### Requirement: Page through every source layer to exhaustion

**Reason**: Retrieval from the source moves out of this capability entirely. This capability now reads complete layers from the mirror and issues no request to HRM, so there is nothing here to page.

**Migration**: `hrm-data-mirror` carries paging, in its requirement "Load the mirror by offset paging", which also replaces identifier-chunk retrieval with offset paging on the measured grounds that the latter is roughly twelve times faster per row.

### Requirement: Survive transient source failures

**Reason**: Retry belongs where the network is, and after this change there is no network in this capability.

**Migration**: `hrm-data-mirror` carries retry. The asymmetry this requirement was written to correct — the census fetch calling the service directly without the retry wrapper every other fetch used — disappears rather than moving, because the census layer is read locally like every other layer.
