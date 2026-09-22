## MODIFIED Requirements

### Requirement: Run unattended

The mirror SHALL initialise itself, populate itself and keep itself current with no human involvement and no source credentials, and SHALL record the outcome of every attempt.

A deployment of the application against a database that has never held the mirror SHALL need no manual step between "the service is running" and "the mirror is populated": the structure the mirror needs is created, an initial attempt to mirror every layer is made, and further attempts follow on a schedule. The schedule SHALL be provided by the deployed system itself and not by an operator remembering to run a command.

The first attempt and every later attempt SHALL be the same operation. Whether a layer is loaded whole or merely polled is decided by what the mirror already holds and what the source has published, never by which kind of attempt it is.

#### Scenario: Scheduled sync runs unattended

- **WHEN** the schedule fires
- **THEN** the sync runs without source credentials and records its outcome

#### Scenario: A fresh deployment populates itself

- **WHEN** the system is deployed against a database that holds no mirror structure and no data
- **THEN** with no operator action the structure is created, every layer is loaded, and the mirror reaches the state of holding a completed version of each layer

#### Scenario: A restart during the first load

- **WHEN** the deployed system is restarted while the first load is part-way through
- **THEN** it resumes the load from the last completed page rather than starting again

#### Scenario: The source is unreachable at first start

- **WHEN** the first attempt cannot reach the source
- **THEN** the failure is recorded, the mirror remains empty, and a later attempt is made without operator action

## ADDED Requirements

### Requirement: Never run two syncs at once

At most one sync, load or reload of the mirror SHALL be running at any time, whatever started it: the scheduled runner, a deployment that overlaps the previous one, or an operator running a command by hand.

A run that finds another already in progress SHALL NOT touch the mirror's data or its staging copy. It SHALL stop and say that another run is in progress. A scheduled runner SHALL treat this as "not this time", record nothing as a failure, and try again at its next opportunity; a command run by hand SHALL exit with a non-success status so the operator sees it.

The exclusion SHALL disappear when the run holding it ends for any reason, including a crash, so that a dead run cannot block every later one.

#### Scenario: A manual reload during a scheduled run

- **WHEN** an operator starts a reload while the scheduled runner is part-way through a sync
- **THEN** the reload stops without changing anything and reports that another run is in progress

#### Scenario: Two deployments overlap

- **WHEN** a new deployment starts its first attempt while the previous deployment is still syncing
- **THEN** only one of them proceeds, and the other waits for its next scheduled opportunity

#### Scenario: A run dies while holding the exclusion

- **WHEN** the process performing a sync is killed part-way through
- **THEN** the next attempt is not blocked by the dead run and resumes as any interrupted run would

### Requirement: Retry by schedule, not by loop

A failed attempt SHALL be recorded as a failed attempt, and the next scheduled attempt SHALL be its retry. The system SHALL wait at least five minutes before attempting again after a failure and SHALL NOT retry immediately or in a tight loop, so that an outage or a rate limit at the source is never met with a burst of requests.

A failure while attempting one layer SHALL NOT prevent the other layers from being attempted in the same run. The run as a whole SHALL be recorded as failed if any layer failed, and SHALL NOT be reported as a successful sync.

Products derived from a run, the retained lists and the per-type figures, SHALL be produced only from a mirror whose two Cityworks layers are at the same published version. A run in which one of those layers reloaded and the other failed SHALL NOT produce them.

#### Scenario: A failed attempt is retried later

- **WHEN** an attempt fails
- **THEN** the failure is recorded, the last successful sync time is unchanged, and no further attempt begins for at least five minutes

#### Scenario: One layer fails

- **WHEN** the census layer cannot be polled during a run
- **THEN** the two Cityworks layers are still polled and reloaded if they advanced, and the run is recorded as failed

#### Scenario: A Cityworks pair left half-reloaded

- **WHEN** the service requests layer reloads and the custom fields layer then fails
- **THEN** the run is recorded as failed and no retained lists or per-type figures are produced from that mixed state

### Requirement: Publish whether the mirror is ready to be read

The system SHALL expose the mirror's readiness as one of four states, so that a consumer can tell an empty mirror that is being filled from one that is broken:

- **uninitialised**: the structure the mirror needs does not yet exist.
- **awaiting first load**: the structure exists, no completed version of every layer is held, and no sync is running now.
- **loading**: the structure exists, no completed version of every layer is held, and a sync is running now.
- **ready**: a completed version of every layer is held.

A mirror that holds a completed version is **ready** while a later reload is in progress; the reload is not a state of its own, because the version held stays whole and queryable until it is replaced.

Readiness SHALL be determined from what is actually happening, not from a record that could outlive the run it describes: a run that died SHALL NOT leave the mirror reported as loading.

#### Scenario: Before anything has run

- **WHEN** a consumer asks for readiness against a database with no mirror structure
- **THEN** it is told the mirror is uninitialised, and the request does not fail

#### Scenario: During the first load

- **WHEN** the first load is running and no layer has completed
- **THEN** the mirror is reported as loading

#### Scenario: The first load has failed and nothing is running

- **WHEN** the first attempt failed and the next has not begun
- **THEN** the mirror is reported as awaiting first load, not as loading

#### Scenario: A later reload

- **WHEN** a completed version is held and a reload of one layer is running
- **THEN** the mirror is reported as ready

#### Scenario: The loading run died

- **WHEN** the process that was performing the first load has been killed
- **THEN** the mirror is reported as awaiting first load rather than as loading
