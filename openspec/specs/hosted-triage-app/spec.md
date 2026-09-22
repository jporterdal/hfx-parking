# hosted-triage-app Specification

## Purpose

Serves the doorway list, the block list and the map at a URL, so that HRM coordinators and parking enforcement officers can open the same list from their own devices and triage it together.

The capability it adds over the generated file is shared state: decisions reach colleagues rather than sitting in the browser that made them. The capability it must not lose is honesty — about how fresh the data is, about what a role selection does and does not mean, and about every interpretation limit the analysis already carries.

## Requirements

### Requirement: Serve the lists over HTTP

The system SHALL serve the doorway list, the block list and the map as an application reachable at a URL, requiring nothing of a viewer beyond a browser.

#### Scenario: A viewer opens the application

- **WHEN** a viewer navigates to the URL
- **THEN** the doorway list, the block list and the map are available with no install, no account and no credentials

### Requirement: Route by canonical violation type

The application SHALL address each tracked canonical violation type by its own route, and SHALL scope every list, ranking, map marker and triage decision to the type being viewed.

Rows from one type SHALL NOT appear in another type's view. The types are not pooled, because the evidence supporting the product's framing was measured per type and does not transfer between them.

#### Scenario: Switching types

- **WHEN** a viewer moves from one violation type's route to another
- **THEN** the lists, rankings, map markers and triage decisions shown are entirely those of the type now selected

#### Scenario: A type's own conclusion

- **WHEN** a type's effectiveness evidence does not support the framing established for blocked driveway
- **THEN** that type's view states its own conclusion rather than inheriting another type's

#### Scenario: An untracked type

- **WHEN** a route names a violation type that is not tracked
- **THEN** the application reports it as untracked rather than rendering an empty list

### Requirement: Present both lists with their per-row detail

The application SHALL present the ranked doorway list and the ranked block list, and SHALL let a viewer move between them.

Each doorway SHALL show its call counts, tows, vehicles seen and distinct, median gap, last call, district, community, property ownership, and the count of still-calling doorways on its block. Each block SHALL show its doorway count, call counts, tows, dwellings, per-dwelling rate, district, worst doorway, and member addresses.

#### Scenario: Both lists reachable

- **WHEN** a viewer opens a violation type's view
- **THEN** they can view the doorway list and the block list

#### Scenario: Doorway detail shown

- **WHEN** a doorway is selected
- **THEN** its counts, tows, vehicle figures, gap, last call, district, community, ownership, and block neighbour count are shown

#### Scenario: Block membership shown

- **WHEN** a block is selected
- **THEN** its member addresses and its worst doorway are shown

### Requirement: Locate each doorway on a map of Halifax

The application SHALL show a zoomable map drawn from HRM's street network, and SHALL place the listed doorways on it, so a viewer can see where a doorway or block sits.

#### Scenario: Doorway placed on the map

- **WHEN** a doorway with a location is displayed
- **THEN** it appears at that location on the map

#### Scenario: Map is zoomable

- **WHEN** a viewer zooms the map
- **THEN** the street network redraws at the new scale

#### Scenario: Doorway without a location

- **WHEN** a doorway has no coordinates
- **THEN** it remains in the list and is not placed at a fabricated location

### Requirement: Persist triage decisions to shared storage

A triage decision, and any note accompanying it, SHALL be stored server-side and SHALL be visible to every viewer of that type's list.

This replaces a fallback in which decisions persisted only to the browser that made them. That fallback SHALL NOT be reachable in the hosted application: a viewer must never be left believing their triage reached colleagues when it did not.

#### Scenario: Two viewers see one list

- **WHEN** one viewer records a decision and another viewer opens the same type's list
- **THEN** the second viewer sees the first viewer's decision

#### Scenario: Decisions survive a restart

- **WHEN** the application restarts
- **THEN** previously recorded decisions are still present

#### Scenario: Shared storage is unavailable

- **WHEN** decisions cannot be persisted
- **THEN** the application says so plainly and does not present an unsaved decision as recorded

### Requirement: Show when a decision was last changed and by which role

Each decision SHALL carry the time it was last changed and the role of the viewer who changed it.

#### Scenario: Attribution is visible

- **WHEN** a viewer inspects a triaged doorway or block
- **THEN** the decision's last-changed time and the role that set it are shown

### Requirement: Identify a viewer by role, not by account

A viewer SHALL select a role — coordinator, parking enforcement officer, or other for a viewer who is neither — before recording a decision, and that role SHALL be attributed to the decisions they record.

The application SHALL state that role selection identifies rather than authenticates, and SHALL NOT present it as access control. Anyone who can reach the URL can record a decision; a viewer who believes otherwise is misled about who can change their work list.

#### Scenario: Role is selected before the first decision

- **WHEN** a viewer records a decision without having selected a role
- **THEN** they are asked to choose one first, and the decision is recorded with that role

#### Scenario: Role is not a credential

- **WHEN** a viewer selects a role
- **THEN** the application states that roles identify rather than authenticate, and grants no different access

#### Scenario: Reading needs no role

- **WHEN** a viewer has selected no role
- **THEN** the lists and the map are still readable

### Requirement: Display data freshness prominently, and name whose limit it is

Every view SHALL carry a prominent message stating when the data was last successfully updated and when the next update is expected.

The message SHALL distinguish a lag at the source from a failure in this system. Where the data is behind because HRM has not published anything newer, the message SHALL say so in those terms — that the limit is the data source's publishing schedule, not this system's. A viewer reading a five-day-old figure needs to know which of those two situations they are in, because one is a property of HRM's open data and the other is a bug, and only one of them is a reason to distrust the figure.

Where the expected next source update is not yet known, the message SHALL say so rather than estimating one.

#### Scenario: Freshness is shown without being sought

- **WHEN** a viewer opens any list
- **THEN** the last successful update and the expected next update are prominently visible on that view

#### Scenario: The source is the limit

- **WHEN** this system is syncing successfully but HRM has not published for several days
- **THEN** the message attributes the lag to the data source's publishing schedule rather than presenting it as this system being out of date

#### Scenario: This system is the limit

- **WHEN** HRM has published more recently than the last successful sync
- **THEN** the message states that this system is behind the source

#### Scenario: Next update is unknown

- **WHEN** too little history exists to predict the next source update
- **THEN** the message states that the next update time is not yet known

#### Scenario: Freshness is not inferred from availability

- **WHEN** the application is serving normally but its data is stale
- **THEN** the view still reports the data as stale

### Requirement: Treat the next update as a state, not a rendered date

A displayed next-update time is a promise about a scheduler that may have stopped. The application SHALL NOT continue displaying a future update time once that time has passed.

Where the scheduled update time has passed by a grace period with no successful sync, the message SHALL change to an overdue state naming how long it has been overdue.

This exists because the alternative failure is invisible: a dead scheduler leaves a cheerful future date rendering indefinitely, never arriving, while the application otherwise behaves perfectly.

#### Scenario: A scheduled update did not happen

- **WHEN** the next update time has passed by the grace period with no successful sync
- **THEN** the message reads as overdue and names how long it has been overdue

#### Scenario: A stale date is never shown as pending

- **WHEN** the scheduler has stopped
- **THEN** no past time is displayed as an upcoming update

### Requirement: Filter the lists interactively

The application SHALL let a viewer adjust, as controls rather than as a rebuild: the district, the minimum recent calls required for a doorway to be listed, the minimum still-calling doorways required for a block to be listed, the recency window, and the recurrence window.

Results SHALL update to reflect the selected values, and the ranking SHALL remain correct under them.

The recency window and the recurrence window SHALL be presented as two distinct controls, each labelled by what it governs. They are not the same window despite sharing a default of 365 days: the recency window decides which doorways are listed at all and feeds the recent-call count the ranking is computed from, while the recurrence window only defines what counts as a repeat call in a published column. Presenting them as one control would misrepresent both — moving it would not change the list the viewer is reading, while silently changing a figure they may not be.

Every view and every export SHALL state the filter values that produced it. A threshold is part of what a figure means — a doorway list at a minimum of two recent calls and the same list at a minimum of five are different claims, and one is indistinguishable from the other unless the view says which it is.

Where a filter combination yields nothing, the application SHALL say so and name the values that excluded everything, so a viewer can tell an empty result from a broken one.

#### Scenario: A coordinator narrows to their district

- **WHEN** a viewer selects a district
- **THEN** the doorway and block lists show only that district, correctly ranked

#### Scenario: A threshold is widened

- **WHEN** a viewer lowers the minimum recent calls
- **THEN** doorways below the previous threshold appear, ranked among the others

#### Scenario: Filter values travel with the figures

- **WHEN** a viewer reads or exports a filtered list
- **THEN** the filter values that produced it are stated on it

#### Scenario: The two windows are distinguishable

- **WHEN** a viewer adjusts the recency window
- **THEN** the set of listed doorways and the ranking change, and the repeat-call definition does not

#### Scenario: The recurrence window governs only the repeat figure

- **WHEN** a viewer adjusts the recurrence window
- **THEN** the repeat-call figure changes and the set of listed doorways does not

#### Scenario: A filter excludes everything

- **WHEN** no doorway or block meets the selected values
- **THEN** the application states that nothing met the threshold and names the values in effect

### Requirement: State the recency window's anchor

Where a list is restricted to doorways still calling within a recent window, the view SHALL state the date that window is measured from.

#### Scenario: The anchor is named

- **WHEN** a viewer reads a list filtered to recent activity
- **THEN** the view names the date the window is measured from and the length of the window

### Requirement: Carry the interpretation limits into the served views

The application SHALL publish, with the lists themselves, the limits the analysis rests on: that the initiation timestamp is a staff intake clock rather than when the problem occurred; that the tow flag is the only published enforcement outcome, so a call with no tow has no recorded outcome rather than no action; that vehicle identity is derived from make, model and colour rather than a plate, making a distinct count a floor; that addresses are reduced as text rather than geocoded, so one doorway can split across rows; that a block's street label is derived rather than official; and that a block's per-dwelling rate spans the whole block rather than the street the calls sit on.

These limits travel with the figures because a view circulates without its context, and a hosted view circulates more widely than a file did.

#### Scenario: Limits accompany the figures

- **WHEN** a viewer reads any list
- **THEN** the limits bearing on the figures in it are available from that view

#### Scenario: No claim beyond the data

- **WHEN** the application reports enforcement outcomes
- **THEN** it claims no outcome from the resolution field and no time-of-day finding from pooled timestamps

### Requirement: Explain the block neighbour count before a remedy is chosen

Each doorway SHALL carry the count of still-calling doorways sharing its block, and the view SHALL explain how to read it: a doorway alone on its block is a candidate for a single physical fix, while a doorway among many is a block-level problem that a fix at one address will not solve.

#### Scenario: The count is readable as guidance

- **WHEN** a viewer reads a doorway with several still-calling neighbours
- **THEN** the view explains that the block rather than the doorway is the unit of remedy

### Requirement: Serve map geometry as a cacheable asset

The street network geometry SHALL be served as an asset the browser can cache across views, rather than embedded into each view's payload.

#### Scenario: Geometry is fetched once

- **WHEN** a viewer moves between violation types
- **THEN** the street network is not re-downloaded for each type

### Requirement: Remain legible in light and dark

The application SHALL render legibly under both light and dark colour-scheme preferences and SHALL follow a change of preference while open.

#### Scenario: Preference changes while open

- **WHEN** the viewer's colour-scheme preference changes with the application open
- **THEN** the application follows it without requiring a reload

### Requirement: Export a list as a file

The application SHALL offer the doorway and block lists for a tracked type as downloadable files: a complete machine-readable table, and a readable brief.

The generated file it replaces was mailable and archivable, and those properties are why a person who cannot be given a URL can still be given the list. Because no scheduled job commits generated output any more, the download is the only way a list leaves the application.

#### Scenario: A complete table is exported

- **WHEN** a viewer exports a type's doorway list
- **THEN** the file carries every listed doorway, not only those displayed

#### Scenario: An export carries its provenance

- **WHEN** a viewer exports any list
- **THEN** the file states the last successful sync time, the most recent call date, the violation type it covers, and the same interpretation limits the view carries

#### Scenario: A view and an export agree

- **WHEN** a viewer exports a list from a view
- **THEN** every count in the export matches the view it came from

#### Scenario: Both name the same mirror state

- **WHEN** a view and an export are compared
- **THEN** both state the same last successful sync time and most recent call date
