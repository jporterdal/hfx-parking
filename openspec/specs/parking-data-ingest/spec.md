# parking-data-ingest Specification

## Purpose

Joins the three Halifax Regional Municipality open datasets that hold a parking complaint, its outcome, and the neighbourhood it sits in, which HRM publishes apart with no join between them, and normalizes the result so every downstream consumer reads one clean set of calls.

## Requirements

### Requirement: Select calls by alleged violation

The violation type lives in the custom-fields table, not on the call record, so selection starts there: the system SHALL find the request identifiers whose alleged violation matches a caller-supplied label, then retrieve those requests.

Matching SHALL be by substring, case-insensitively, because HRM records one problem under more than one label. A blocked driveway is filed as both `Blocking Driveway (DISPATCH)` and `DRIVEWAY`; a rule that only strips the `(DISPATCH)` suffix would leave the two as separate types and undercount the problem.

The system SHALL report which labels the match resolved to, so an operator can see what was actually included.

#### Scenario: Multiple labels for one problem

- **WHEN** calls are selected for the violation substring `Driveway`
- **THEN** both `Blocking Driveway (DISPATCH)` and `DRIVEWAY` are included, and their combined total is reported

#### Scenario: Matched labels are disclosed

- **WHEN** a selection runs
- **THEN** the distinct alleged-violation labels it matched are reported before the results are used

#### Scenario: Violation is caller-supplied

- **WHEN** a different violation substring is supplied
- **THEN** the same pipeline runs unchanged against that violation

### Requirement: Resolve a violation label to its canonical type

A canonical violation type is a group of one or more raw `Alleged Violation` labels that name the same problem under different spellings — a current mixed-case label, a legacy uppercase short code, and sometimes a `(DISPATCH)` suffix variant. The system SHALL resolve a requested canonical type to every raw label it maps to before selecting calls, rather than requiring the caller to enumerate them.

The `Other` catch-all and any label that does not describe where a vehicle is stopped (such as an idling complaint) SHALL NOT be resolved into a canonical violation type.

#### Scenario: Canonical type resolves to all its raw labels

- **WHEN** a canonical violation type maps to more than one raw label
- **THEN** calls filed under any of those raw labels are included in its selection

#### Scenario: Ambiguous or non-parking labels excluded

- **WHEN** the canonical violation type list is built
- **THEN** the `Other` catch-all and labels that do not describe vehicle placement are not included as tracked types

### Requirement: Page through every source layer to exhaustion

The source layers cap rows per response. The system MUST page until a response returns fewer rows than the page size, and MUST NOT assume one response holds the full result.

Page sizes differ by layer: the request and custom-field tables return up to 1,000 rows, while the census layer returns fewer when geometry is requested. The system SHALL page each layer at a size that layer accepts.

#### Scenario: Multi-page result retrieved whole

- **WHEN** a selection matches more rows than one response can carry
- **THEN** every row is retrieved

#### Scenario: Census geometry paged at its own limit

- **WHEN** census polygons are retrieved with geometry
- **THEN** paging uses a size that layer accepts rather than the size used for the tabular layers

### Requirement: Survive transient source failures

The sources are public HTTP services with no availability guarantee. The system SHALL retry a failed request a bounded number of times before giving up, and SHALL fail with the source error rather than silently returning partial data.

#### Scenario: Transient failure retried

- **WHEN** a request fails once and succeeds on retry
- **THEN** retrieval continues and the result is complete

#### Scenario: Persistent failure surfaces

- **WHEN** a request fails every attempt
- **THEN** the run stops and reports the source error, rather than producing outputs from partial data

### Requirement: Attach outcome and vehicle fields to each call

The system SHALL attach to each selected call, from the custom-fields table, the alleged violation, whether the vehicle was towed, the property ownership, and the vehicle make, model and colour.

A call missing any of these SHALL be retained with that attribute absent rather than discarded.

#### Scenario: Fields attached

- **WHEN** a selected call has custom-field rows
- **THEN** it exposes alleged violation, tow flag, property ownership, and vehicle make, model and colour as direct attributes

#### Scenario: Call with no vehicle recorded

- **WHEN** a call has no vehicle fields recorded
- **THEN** it is retained, and the absence is distinguishable from a recorded blank

### Requirement: Reduce each address to one doorway key

The address field is free text carrying the community and postal code, so one physical doorway appears under several spellings. The system SHALL reduce each address to a key by taking the text before the first separator and collapsing whitespace, so that spellings differing only by community or postal code group together.

This is a string reduction, not a geocode. The system MUST state that some doorways will still split into more than one key.

#### Scenario: Postal-code variants group together

- **WHEN** two calls carry the same civic address, one with a postal code and one without
- **THEN** both reduce to the same doorway key

#### Scenario: Residual splitting disclosed

- **WHEN** results are published
- **THEN** they state that address matching is a string reduction and some doorways may still appear as separate rows

#### Scenario: Call with no address

- **WHEN** a call carries no address
- **THEN** it contributes to no doorway and is excluded from the doorway grouping

### Requirement: Locate each doorway by a representative coordinate

A single mistyped coordinate on one call would misplace a doorway. The system SHALL locate each doorway at the median of its calls' coordinates, so one bad point cannot move it.

A doorway whose calls carry no coordinates SHALL have no location, and MUST NOT be assigned one by guesswork.

#### Scenario: Outlier coordinate does not move the doorway

- **WHEN** one call at a doorway carries a coordinate far from the others
- **THEN** the doorway's location is the median and is unaffected by that call

#### Scenario: Doorway with no coordinates

- **WHEN** none of a doorway's calls carry coordinates
- **THEN** the doorway has no location and is not placed in a neighbourhood

### Requirement: Place each doorway in a census neighbourhood

The system SHALL assign each located doorway to the census dissemination area containing it, determined by point-in-polygon containment against the census layer's own polygons.

Containment MUST account for polygons with interior holes, so a doorway inside a hole is not counted as inside the polygon. The system SHALL carry the neighbourhood's identifier and its dwelling count forward, because the dwelling count is the denominator that keeps a dense neighbourhood from outranking a worse one.

The system SHALL report how many doorways fell in no neighbourhood.

#### Scenario: Doorway assigned to its neighbourhood

- **WHEN** a located doorway falls inside a census polygon
- **THEN** it carries that polygon's identifier and dwelling count

#### Scenario: Interior hole excluded

- **WHEN** a doorway falls inside a hole within a polygon
- **THEN** it is not assigned to that polygon

#### Scenario: Unplaced doorways reported

- **WHEN** a run completes
- **THEN** the number of doorways that fell in no neighbourhood is reported

### Requirement: Convert timestamps to Atlantic local time with daylight saving

Source timestamps are UTC. Halifax observes Atlantic time with daylight saving, so its offset is three hours in summer and four in winter.

The system SHALL convert using daylight-saving-aware conversion for the date of each timestamp. A single fixed offset MUST NOT be used: the analysed range spans multiple years, so a fixed offset misplaces every timestamp outside the season it was chosen for by one hour.

#### Scenario: Winter and summer timestamps both correct

- **WHEN** two calls are initiated at the same local clock time, one in July and one in January
- **THEN** both report the same local time

#### Scenario: Fixed offset rejected

- **WHEN** timestamps are converted
- **THEN** the offset applied reflects whether daylight saving was in effect on that date, not one constant for the whole range

### Requirement: Warn that the initiation timestamp is an intake clock

The initiation timestamp records when a staff member entered the call, not when the problem occurred. On the staff-entered channel almost no calls are recorded overnight, while the citizen-entered channel spreads across all hours.

The system SHALL expose the initiating channel alongside every call, and any published finding that uses time of day MUST either split by channel or state that the pooled figure reflects intake hours.

#### Scenario: Channel exposed

- **WHEN** a call is retrieved
- **THEN** its initiating channel is available to consumers

#### Scenario: Hour-of-day finding qualified

- **WHEN** a finding derived from time of day is published
- **THEN** it is split by initiating channel, or states that the pooled timestamp reflects staff intake hours

### Requirement: Stamp every run with its provenance

Outputs are regenerated on a schedule and committed automatically, so a reader cannot tell a fresh result from a stale one without a stamp. The system SHALL record, and make available to every output it writes, the time the run executed and the date of the most recent call in the data.

Where a window is measured relative to the data rather than to the wall clock, the system SHALL state the date that window is anchored to, so a stalled pipeline is visible rather than silently sliding the window backwards.

#### Scenario: Run timestamp available to outputs

- **WHEN** a run writes its outputs
- **THEN** each output carries the time the run executed and the date of the most recent call

#### Scenario: Window anchor stated

- **WHEN** a recency window is applied
- **THEN** the date it is anchored to is stated with the results

#### Scenario: Stalled pipeline is visible

- **WHEN** the source stops updating and a run produces the same most-recent-call date as the previous run
- **THEN** a reader can tell from the outputs that the data has not advanced
