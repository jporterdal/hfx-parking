## Purpose

Produces the ranked list of individual doorways where parking enforcement has already been tried and has not worked, filtered to those still generating calls, so the team that installs signs, bollards and curb paint has a work list rather than a dashboard.

## ADDED Requirements

### Requirement: Group calls by doorway

The system SHALL group calls by their doorway key, so each listed row is one physical address rather than one call.

The doorway is the unit of the list because the fix is physical and installed at one address. A coarser grouping cannot say where to put a bollard.

#### Scenario: Repeat calls collapse to one row

- **WHEN** a doorway has many calls over several years
- **THEN** it appears as a single row carrying its call counts

#### Scenario: Row identifies a physical address

- **WHEN** a row is read
- **THEN** it names an address specific enough to dispatch a physical fix to

### Requirement: Drop doorways that have stopped calling

A work list must contain only live problems. The system SHALL exclude any doorway with no call inside the recency window, and SHALL NOT rank a doorway on all-time volume alone.

The recency window SHALL be configurable and default to twelve months.

#### Scenario: Dormant doorway excluded

- **WHEN** a doorway's most recent call falls outside the recency window
- **THEN** it does not appear on the list, regardless of how many calls it has all time

#### Scenario: Still-calling doorway retained

- **WHEN** a doorway has at least one call inside the recency window
- **THEN** it is eligible for the list

### Requirement: Require a minimum number of recent calls

A doorway that called once is not yet a pattern. The system SHALL require a configurable minimum number of calls inside the recency window, defaulting to two, before a doorway is listed.

#### Scenario: Single recent call excluded by default

- **WHEN** a doorway has exactly one call inside the recency window and the minimum is two
- **THEN** it is not listed

#### Scenario: Minimum is configurable

- **WHEN** the minimum is raised
- **THEN** fewer doorways are listed, and the count is reported

### Requirement: Carry the evidence that enforcement has not worked

Each listed doorway SHALL carry the figures a reader needs to judge it without opening the source data:

- calls inside the recency window, and calls all time;
- tows recorded;
- vehicles seen and distinct vehicles among them;
- repeat calls, and the median gap in days between consecutive calls;
- the date of the most recent call;
- the district, the community, the street, and the predominant property ownership.

#### Scenario: Row carries its evidence

- **WHEN** a doorway is listed
- **THEN** it carries recent and all-time call counts, tows, vehicles seen and distinct, repeat calls, median gap, last call date, district, community, street, and property ownership

#### Scenario: Vehicles seen precedes vehicles distinct

- **WHEN** a doorway has vehicle data on only a few of its calls
- **THEN** the vehicles-seen figure is available alongside the distinct count, so the distinct count is not read as covering every call

### Requirement: Rank by calls still arriving, discounted by enforcement already applied

The system SHALL rank doorways by their recent call count weighted by how little enforcement has achieved there, so that a doorway already being towed ranks below one where calls keep arriving and nothing has been recorded.

Ranking MUST NOT be by all-time volume, which promotes addresses that stopped calling years ago.

#### Scenario: Untouched doorway outranks a towed one

- **WHEN** two doorways have equal recent call counts and one has a high tow rate
- **THEN** the one with the lower tow rate ranks higher

#### Scenario: Stale volume does not rank

- **WHEN** a doorway has a large all-time count but few recent calls
- **THEN** it ranks below a doorway with more recent calls

### Requirement: Tell each doorway how many neighbours are also calling

The number of other still-calling doorways on the same neighbourhood block is the figure that decides whether the fix is a single installation or a block-wide measure. The system SHALL carry that count on every listed doorway.

#### Scenario: Isolated doorway

- **WHEN** a listed doorway is the only one still calling on its block
- **THEN** its neighbour count reads one, indicating a single physical fix is appropriate

#### Scenario: Doorway on a problem block

- **WHEN** several listed doorways share a block
- **THEN** each carries the count of still-calling doorways on that block, indicating a block-wide measure rather than one installation per address

#### Scenario: Doorway in no neighbourhood

- **WHEN** a listed doorway was not placed in any neighbourhood
- **THEN** its neighbour count defaults to one rather than being left absent

### Requirement: Allow the list to be narrowed to one district

The system SHALL support restricting the list to a single council district, so a district's own doorways can be reviewed on their own.

#### Scenario: District filter applied

- **WHEN** the list is restricted to one district
- **THEN** only doorways in that district are listed

#### Scenario: Empty result reported

- **WHEN** no doorway meets the thresholds after filtering
- **THEN** the run reports that nothing met the threshold rather than writing an empty list as though it were a finding
