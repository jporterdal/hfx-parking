# enforcement-effectiveness Specification

## Purpose

Establishes and publishes the evidence that enforcement does not resolve these addresses, by comparing recurrence after a tow against recurrence without one and by measuring how often the same vehicle is involved, so the product's central claim rests on figures a reader can check.

## Requirements

### Requirement: Measure recurrence at a doorway

The system SHALL define recurrence as a further call at the same doorway within a configurable number of days, defaulting to a year, and SHALL count it per doorway and across the whole selection.

#### Scenario: Recurrence counted

- **WHEN** a doorway has two calls within the recurrence window
- **THEN** the earlier call is counted as recurring

#### Scenario: Window is configurable and stated

- **WHEN** recurrence figures are published
- **THEN** the window they used is stated

### Requirement: Compare recurrence with and without a tow

The system SHALL report recurrence separately for calls that ended in a tow and calls that did not, so the two rates can be read against each other.

Where the two rates are close, the system SHALL state the bound the data places on any real effect rather than reporting only that no difference was found. An absence of difference in a sample this size is not the same as proof of no effect at any size.

#### Scenario: Rates reported side by side

- **WHEN** the comparison is published
- **THEN** it reports the call count and recurrence rate for towed and for not-towed calls

#### Scenario: Effect bound stated

- **WHEN** the two recurrence rates are close
- **THEN** the published figure states the magnitude of effect the data can rule out

#### Scenario: Comparison described as observational

- **WHEN** the comparison is published
- **THEN** it states that the comparison is observational and that tows may cluster at the worst addresses, which would mask a real effect

### Requirement: Measure how often the same vehicle recurs

The system SHALL derive a vehicle identity from the recorded make, model and colour, and SHALL report, across the listed doorways, how many distinct vehicles account for how many calls.

A call with none of those fields recorded SHALL contribute to neither count, and the number of calls that did carry vehicle data SHALL be reported alongside the distinct count.

#### Scenario: Uniqueness reported

- **WHEN** vehicle figures are published
- **THEN** they report distinct vehicles against the number of calls carrying vehicle data

#### Scenario: Distinct count is a floor

- **WHEN** vehicle figures are published
- **THEN** they state that identity is make, model and colour rather than a plate, so two identical vehicles count as one and the distinct count is a floor

#### Scenario: Sparse vehicle data disclosed

- **WHEN** a doorway records vehicle data on only a small share of its calls
- **THEN** its distinct count is presented with the number of calls it was drawn from, so it is not read as covering every call

### Requirement: Treat the tow flag as the only published enforcement outcome

HRM publishes no ticketing field: there is none among the custom field names and the resolution field carries no ticket value. The tow flag is therefore the only recorded enforcement action.

The system SHALL use the tow flag as the outcome signal, and MUST NOT treat the resolution field as evidence that enforcement action was taken. A call with no tow SHALL be described as a call with no recorded outcome, not as a call where nothing was done.

#### Scenario: Resolution not used as an outcome

- **WHEN** outcomes are classified
- **THEN** the resolution field is not read as indicating that an enforcement action occurred

#### Scenario: No tow stated honestly

- **WHEN** a call with no tow is reported
- **THEN** it is described as carrying no recorded outcome rather than as proof that nothing was done

#### Scenario: Share with no recorded outcome reported

- **WHEN** outcome figures are published
- **THEN** the share of calls carrying no recorded enforcement outcome is reported

### Requirement: Report how quickly calls are closed

The elapsed time between a call being initiated and closed is the municipality's own service measure and is published as part of the product's framing, so it MUST be derived from the data rather than asserted.

The system SHALL compute the elapsed time from the initiation and closure timestamps and SHALL report a median across the selection. Any such figure published in project documentation MUST come from this computation.

#### Scenario: Response time computed

- **WHEN** the selection is analysed
- **THEN** a median elapsed time from initiation to closure is computed and reported

#### Scenario: Calls never closed excluded

- **WHEN** a call carries no closure timestamp
- **THEN** it is excluded from the elapsed-time median and counted separately

#### Scenario: Published figure is reproducible

- **WHEN** an elapsed-time figure appears in project documentation
- **THEN** running the pipeline reproduces it

### Requirement: Compute effectiveness evidence independently per violation type

Recurrence, the tow comparison, and vehicle uniqueness SHALL be computed separately for each canonical violation type. A finding from one type MUST NOT be presented as evidence for another.

#### Scenario: Each type measured on its own calls

- **WHEN** effectiveness evidence is published for a canonical violation type
- **THEN** every figure in it is computed from that type's own calls, not from another type's

#### Scenario: A type's own numbers decide its own conclusion

- **WHEN** two canonical violation types are compared
- **THEN** neither type's published conclusion is justified by the other type's recurrence or vehicle figures

### Requirement: Point the conclusion at physical remedies

Where recurrence is unchanged by a tow and the vehicles involved are overwhelmingly distinct, there is no repeat offender for enforcement to deter. The system SHALL state that conclusion and SHALL direct its output to the team responsible for signs, bollards and curb markings.

The system MUST NOT present its output as an enforcement shift plan or patrol schedule.

#### Scenario: Conclusion stated with its evidence

- **WHEN** the product's conclusion is published
- **THEN** it is accompanied by the tow comparison and the vehicle uniqueness figures it rests on

#### Scenario: Not presented as a patrol plan

- **WHEN** output is published
- **THEN** it is addressed to whoever installs physical remedies, and is not framed as an officer's shift or patrol plan

#### Scenario: Enforcement audience still served

- **WHEN** output is published
- **THEN** it remains readable as evidence that these particular calls are not resolvable by enforcement

#### Scenario: Conclusion withheld where evidence does not support it

- **WHEN** a canonical violation type's tow comparison or vehicle uniqueness does not resemble driveway's (a large recurrence gap after a tow, or vehicles that substantially repeat)
- **THEN** that type's output states plainly that a physical remedy is not supported by its evidence, rather than repeating driveway's conclusion
