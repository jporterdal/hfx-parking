# text-briefs Specification

## Purpose

Writes the doorway and block lists as readable briefs and as machine-readable tables, so the findings can be pasted into a message, printed, or joined against other data without opening the board.

## Requirements

### Requirement: Write a readable brief and a full table for each list

For the doorway list and for the block list, the system SHALL write a readable brief containing a bounded top section, and a separate machine-readable table containing every listed row.

The brief is for a person deciding what to do on Monday; the table is for joining and filtering. Truncating the table would lose rows the brief's own totals refer to.

#### Scenario: Both forms written per list

- **WHEN** a run completes
- **THEN** it has written a readable brief and a complete machine-readable table for the doorway list and for the block list

#### Scenario: Brief is bounded, table is complete

- **WHEN** more rows are listed than the brief displays
- **THEN** the brief shows a bounded top section and states the full total, while the table carries every row

#### Scenario: Block outputs omitted when no block qualifies

- **WHEN** no block meets the minimum doorway count
- **THEN** the block outputs are not written, and the doorway outputs are still produced

### Requirement: Carry the same figures the board carries

Each brief and table SHALL carry the figures defined for its list, so a reader working from a file reaches the same conclusion as one working from the board.

#### Scenario: Doorway rows carry their evidence

- **WHEN** the doorway brief is read
- **THEN** each row shows its recent and all-time calls, tows, distinct against seen vehicles, median gap, last call, block neighbour count, and district

#### Scenario: Block rows carry their evidence

- **WHEN** the block brief is read
- **THEN** each row shows its streets, doorway count, recent and all-time calls, tows, dwellings, per-dwelling rate, and district

#### Scenario: Briefs agree with the board

- **WHEN** briefs and board are produced by one run
- **THEN** their figures match

### Requirement: Tell the reader how to act on the block neighbour count

The count of still-calling doorways on a block is the figure that decides the remedy, and a reader scanning a table will not infer that on their own.

The doorway brief SHALL state explicitly that a count of one points to a single physical fix, while a higher count means the block is the problem and the block list is what to work from.

#### Scenario: Guidance present

- **WHEN** the doorway brief is read
- **THEN** it states how to interpret the block neighbour count before ordering a remedy

#### Scenario: Reader directed to the block list

- **WHEN** a doorway's block neighbour count is high
- **THEN** the brief directs the reader to the block list rather than to a per-address remedy

### Requirement: State the limits with the findings

Briefs circulate without their context, so the limits MUST travel in the file. Every brief SHALL state:

- that the tow flag is the only published enforcement outcome, and a call with no tow carries no recorded outcome rather than proof nothing was done;
- that vehicle identity is make, model and colour rather than a plate, so the distinct count is a floor;
- the recurrence window used;
- that address matching is a string reduction, so some doorways may split;
- for block outputs, that the block label is derived from call addresses rather than official, and that the per-dwelling rate spans the whole block rather than the street the calls are on.

#### Scenario: Limits stated in the brief

- **WHEN** a brief is written
- **THEN** it states the outcome limit, the vehicle identity limit, the recurrence window, and the address matching limit

#### Scenario: Block limits stated

- **WHEN** the block brief is written
- **THEN** it states that the block label is derived and that the per-dwelling rate is block-wide

#### Scenario: Limits survive extraction

- **WHEN** the ranked table is read on its own
- **THEN** the brief's structure keeps the limits attached rather than placing them where they are easily separated from the findings

### Requirement: State provenance and the source join

Every brief SHALL name the datasets it joined, the canonical violation type it covers, the time the run executed, the date of the most recent call in the data, and the date the recency window is anchored to.

#### Scenario: Provenance present

- **WHEN** a brief is written
- **THEN** it names the joined datasets, the run time, the most-recent-call date, and the window anchor date

#### Scenario: Violation type named

- **WHEN** a brief is written
- **THEN** it states which canonical violation type its figures cover

#### Scenario: Counts of excluded rows reported

- **WHEN** a run completes
- **THEN** it reports how many doorways fell in no census block

### Requirement: Mark outputs as generated

These files are rewritten on every run, including by the scheduled job, so a hand edit would be silently destroyed.

The system SHALL write them to a location documented as generated output, and the project SHALL state that they are not to be hand-edited.

#### Scenario: Generated location documented

- **WHEN** the project's documentation is read
- **THEN** it identifies these files as generated by the pipeline and not to be hand-edited
