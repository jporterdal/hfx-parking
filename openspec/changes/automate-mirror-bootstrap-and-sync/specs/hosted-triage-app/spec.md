## ADDED Requirements

### Requirement: Say when the mirror is not ready

While the mirror is not ready, every view SHALL say so, and SHALL say which situation it is in using the mirror's own readiness state: the mirror does not exist yet, it is being loaded for the first time, or the first load has not succeeded and will be tried again.

An empty mirror SHALL NOT be presented as a finding. A list that is empty because nothing has been loaded SHALL NOT read as "no doorways meet the filters" or as a healthy list with nothing on it. No route SHALL fail with an unhandled server error because the mirror is not ready, including when the mirror's structure does not yet exist. A route that has nothing to return for that reason SHALL answer with a status saying the service is not yet available and a body naming the readiness state, and a page SHALL render the explanation.

The overdue state SHALL NOT be shown for a mirror that has never completed a load. Overdue means a promised update did not happen; before the first load nothing was promised, and a viewer told that a first load is "overdue" would read a normal first-run state as a fault.

A page left open while the mirror is not ready SHALL notice when the mirror becomes ready, within one minute, and SHALL then show the lists, the map and the freshness message without the viewer reloading it or doing anything else. Once the mirror is ready the page SHALL make no further requests for this purpose.

#### Scenario: The application starts before the mirror exists

- **WHEN** a viewer opens any page and the mirror's structure does not exist
- **THEN** the page renders an explanation that the mirror is not yet set up

#### Scenario: A data route is asked for before the mirror exists

- **WHEN** a data route is requested and the mirror's structure does not exist
- **THEN** it answers that the service is not yet available, names the readiness state in the body, and does not fail with an unhandled error

#### Scenario: The first load is in progress

- **WHEN** a viewer opens a list while the mirror is loading for the first time
- **THEN** the view states that the data is being loaded and that lists will appear when it finishes, and the empty lists are not presented as a result

#### Scenario: The first load failed

- **WHEN** a viewer opens a list and the first load has failed with the next attempt still to come
- **THEN** the view states that the first load has not succeeded and will be attempted again, and does not state that an update is overdue

#### Scenario: The mirror becomes ready while a page is open

- **WHEN** the first load completes while a viewer has the page open showing the not-ready message
- **THEN** within one minute the page shows the lists, the map and the freshness message as for any ready mirror, without the viewer reloading it

#### Scenario: A viewer arrives after the mirror is ready

- **WHEN** a viewer opens the page after the first load has completed
- **THEN** the lists, the map and the freshness message are shown as for any ready mirror, with no not-ready message and no polling

#### Scenario: The server cannot be reached while waiting

- **WHEN** a page showing the not-ready message cannot reach the server for a moment
- **THEN** it keeps the message on screen and tries again, and does not present the mirror as ready or as failed

#### Scenario: Empty lists are not shown as a result

- **WHEN** the mirror is not ready
- **THEN** the doorway list, the block list and the map are not shown, so that an empty list cannot be read as "no doorways"

#### Scenario: A ready mirror reloading

- **WHEN** a completed version is held and a reload is running
- **THEN** no not-ready message is shown, and the freshness message is unchanged
