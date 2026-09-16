# triage-board Specification

## Purpose

Delivers the doorway and block lists as a single self-contained page that anyone can open without a server, install or account, carrying a map of Halifax and recording the triage decision made on each doorway and block so a team works one list instead of several.

## Requirements

### Requirement: Ship as one self-contained file

The board SHALL be a single file that opens in a browser with no server, no install, and no account.

Everything it needs to render — the doorway and block data, and the map geometry — SHALL be embedded in that file at build time. The board MUST NOT depend on a running backend or an external tile service to display its map or its lists.

#### Scenario: Opens with no infrastructure

- **WHEN** a person opens the file directly in a browser
- **THEN** the lists and the map render without a server running and without signing in

#### Scenario: Map needs no external service

- **WHEN** the board renders its map
- **THEN** the geometry comes from data embedded in the file rather than from an external tile or map service

### Requirement: Cover every tracked violation type

The board SHALL present every canonical violation type tracked by a run, and SHALL let a viewer switch between them without leaving the file.

Each type's doorway list, block list and map markers SHALL be scoped to that type; the board MUST NOT mix doorways or blocks from different violation types into one ranked list.

#### Scenario: Viewer switches type

- **WHEN** more than one canonical violation type is present in the board's data
- **THEN** a viewer can select a type and see only that type's doorway list, block list and map markers

#### Scenario: Single-type run still renders

- **WHEN** a run tracks only one canonical violation type
- **THEN** the board renders that type without requiring a switcher to be exercised

#### Scenario: Lists never mix types

- **WHEN** a doorway or block is ranked
- **THEN** it is ranked only against others of the same canonical violation type

### Requirement: Build the board from the same run that writes the briefs

The board and the text briefs MUST be produced by one run over one set of figures, so the page a person opens can never disagree with the files beside it.

#### Scenario: Board and briefs agree

- **WHEN** a run writes the board and the briefs
- **THEN** every count on the board matches the corresponding count in the briefs

#### Scenario: Missing build inputs reported

- **WHEN** the board's template or map geometry is absent
- **THEN** the run reports that the board was skipped and names what was missing, and still writes the briefs

### Requirement: Present both the doorway list and the block list

The board SHALL present the ranked doorway list and the ranked block list, and SHALL let a viewer move between them.

Each doorway SHALL show its call counts, tows, vehicles seen and distinct, median gap, last call, district, community, property ownership, and the count of still-calling doorways on its block. Each block SHALL show its doorway count, call counts, tows, dwellings, per-dwelling rate, district, worst doorway, and member addresses.

#### Scenario: Both lists reachable

- **WHEN** the board is open
- **THEN** the viewer can view the doorway list and the block list

#### Scenario: Doorway detail shown

- **WHEN** a doorway is selected
- **THEN** its counts, tows, vehicle figures, gap, last call, district, community, ownership, and block neighbour count are shown

#### Scenario: Block membership shown

- **WHEN** a block is selected
- **THEN** its member addresses and its worst doorway are shown

### Requirement: Locate each doorway on a map of Halifax

The board SHALL show a zoomable map drawn from HRM's street network, and SHALL place the listed doorways on it, so a viewer can see where a doorway or block sits.

#### Scenario: Doorway placed on the map

- **WHEN** a doorway with a location is displayed
- **THEN** it appears at that location on the map

#### Scenario: Map is zoomable

- **WHEN** a viewer zooms the map
- **THEN** the street network redraws at the new scale

#### Scenario: Doorway without a location

- **WHEN** a doorway has no coordinates
- **THEN** it remains in the list and is not placed at a fabricated location

### Requirement: Record a triage decision per doorway and per block

The board SHALL let a viewer record a decision against each doorway and each block, with an optional note, and SHALL show when each decision was last changed.

#### Scenario: Decision recorded

- **WHEN** a viewer records a decision on a doorway
- **THEN** it is retained and shown against that doorway

#### Scenario: Note attached

- **WHEN** a viewer adds a note with a decision
- **THEN** the note is retained with it

#### Scenario: Progress visible

- **WHEN** decisions have been recorded
- **THEN** the board shows how many doorways and blocks have been triaged out of the total

### Requirement: Share decisions where possible and disclose when they are private

A team triaging one list is the point; three people keeping private lists is the failure. The board SHALL share decisions across viewers where the hosting environment provides shared storage, and SHALL fall back to storing them in the viewer's own browser where it does not.

The board MUST state which of the two is in effect, so a viewer is never left believing their decisions reached colleagues when they did not.

#### Scenario: Shared storage available

- **WHEN** the board runs where shared storage is available
- **THEN** decisions are visible to everyone who opens it, and the board says so

#### Scenario: Shared storage unavailable

- **WHEN** the board runs with no shared storage
- **THEN** decisions persist in that viewer's browser only, and the board states that nobody else will see them

#### Scenario: Browser storage refuses

- **WHEN** the viewer's browser refuses to persist data
- **THEN** the board continues to render and does not fail

#### Scenario: Live updates interrupted

- **WHEN** shared updates stop reaching the board
- **THEN** it says so, and the viewer's own decisions continue to save

### Requirement: Show when the board was built

Because the board is regenerated on a schedule and committed automatically, a stale copy is indistinguishable from a current one unless it says when it was made.

The board SHALL display the time of the run that produced it and the date of the most recent call in its data.

#### Scenario: Build time visible

- **WHEN** the board is open
- **THEN** the run time and the most-recent-call date are visible

#### Scenario: Staleness detectable

- **WHEN** a viewer opens a board built some time ago
- **THEN** they can tell from the board itself how old it is

### Requirement: Remain readable in light and dark

The board SHALL render legibly under both light and dark viewer preferences, and SHALL follow a change of preference while open.

#### Scenario: Both themes legible

- **WHEN** the board is opened under either colour-scheme preference
- **THEN** its lists and map are legible

#### Scenario: Preference change followed

- **WHEN** the viewer's colour-scheme preference changes while the board is open
- **THEN** the board redraws to match
