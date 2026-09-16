# block-rollup Specification

## Purpose

Groups the listed doorways into census neighbourhood blocks, so that several doorways all still calling on one block are recognised as a single problem needing a block-wide measure rather than as separate problems each needing their own sign.

## Requirements

### Requirement: Group listed doorways by census neighbourhood

The system SHALL group the listed doorways by the census dissemination area each was placed in.

The census dissemination area is the grouping grain because coarser municipal groupings do not separate anything: the community field on the call record puts the large majority of calls in a single value, and HRM's community boundary layer fails the same way. Street name works and is readable, but is a weaker cut than the block.

#### Scenario: Doorways on one block group together

- **WHEN** two listed doorways on different streets fall in the same census block
- **THEN** they are reported as one block rather than two separate problems

#### Scenario: Doorway with no neighbourhood excluded from rollup

- **WHEN** a listed doorway was not placed in a census block
- **THEN** it is omitted from the block grouping while remaining on the doorway list

### Requirement: Require a minimum number of still-calling doorways per block

One doorway calling on its own is a doorway problem, not a block problem. The system SHALL list only blocks holding at least a configurable minimum of still-calling doorways, defaulting to two.

#### Scenario: Single-doorway block excluded by default

- **WHEN** a block holds exactly one still-calling doorway and the minimum is two
- **THEN** it is not listed as a block

#### Scenario: Minimum is configurable

- **WHEN** the minimum is raised
- **THEN** fewer blocks are listed

### Requirement: Normalize block load by dwellings

Raw call volume rewards a block for having more front doors. The system SHALL carry each block's dwelling count from the census layer and SHALL express its load as calls per thousand dwellings alongside the raw count.

Where a block has no dwelling count, the rate SHALL be reported as unavailable rather than as zero or as a raw count.

#### Scenario: Dense block does not outrank a worse one

- **WHEN** two blocks have similar raw call counts and one has far more dwellings
- **THEN** the one with fewer dwellings shows the higher per-dwelling rate

#### Scenario: Missing dwelling count

- **WHEN** a block carries no dwelling count
- **THEN** its per-dwelling rate is reported as unavailable

#### Scenario: Rate scope stated

- **WHEN** a per-dwelling rate is published
- **THEN** it is stated to be a rate across the whole block, not along the street the calls are on

### Requirement: Carry each block's composition

Each listed block SHALL carry the figures needed to act on it without opening the doorway list:

- the count of still-calling doorways it holds;
- calls inside the recency window and calls all time, summed across those doorways;
- tows recorded across those doorways;
- its dwelling count and per-dwelling rate;
- its district;
- its worst single doorway;
- the addresses of every doorway it holds.

#### Scenario: Block carries its composition

- **WHEN** a block is listed
- **THEN** it carries doorway count, recent and all-time calls, tows, dwellings, per-dwelling rate, district, worst doorway, and its member addresses

#### Scenario: Worst doorway identified

- **WHEN** a block holds several doorways
- **THEN** the one with the most recent calls is named

### Requirement: Name each block by the streets its calls come from

A census identifier means nothing to a reader, and a dissemination area is not a neighbourhood anyone in Halifax names. The system SHALL label each block by the streets its calls predominantly come from.

The label SHALL be stated as derived rather than official, and the census identifier SHALL remain available so the block can be joined back to the census layer.

#### Scenario: Block labelled by its streets

- **WHEN** a block's calls come predominantly from two streets
- **THEN** its label names those streets

#### Scenario: Census identifier retained

- **WHEN** a block is listed
- **THEN** its census identifier is available for joining back to the census layer

#### Scenario: Label described as derived

- **WHEN** block labels are published
- **THEN** they are stated to be derived from call addresses rather than official neighbourhood names

### Requirement: Rank blocks by spread first, then by load per dwelling

The system SHALL rank blocks by how many separate doorways are still calling, and then by calls per thousand dwellings.

Spread ranks first because it is what distinguishes a block-wide problem from one bad address: a block with ten doorways calling cannot be solved by ten separate installations.

#### Scenario: Spread outranks volume

- **WHEN** one block has ten still-calling doorways and another has two doorways with more total calls
- **THEN** the block with ten doorways ranks higher

#### Scenario: Per-dwelling rate breaks ties

- **WHEN** two blocks hold the same number of still-calling doorways
- **THEN** the one with the higher per-dwelling rate ranks higher
