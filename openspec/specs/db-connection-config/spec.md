# db-connection-config Specification

## Purpose

Defines how the system finds and authenticates to its Postgres database: from the environment and a local file that is never committed, with no credential in the repository, and with a clear failure, never a silent fallback or a skipped test, when the database is unconfigured or unreachable.

## Requirements

### Requirement: Read the database connection from the environment

The system SHALL take the database's address, port, user, password and database name from the standard Postgres environment variables `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE`. Every component that reaches the database, namely the sync, the load, the derivation, the served application, the reconciliation and figures commands, the measurement scripts and the test suite, SHALL take its connection from those variables and from nothing else.

The system SHALL NOT read a connection string from any variable, and SHALL NOT hold a default host, user, password or database name of its own.

#### Scenario: Every component uses the same configuration

- **WHEN** the same `PG*` variables are set and any component that reaches the database is run
- **THEN** it connects to the database those variables name

#### Scenario: No built-in default

- **WHEN** no `PG*` variable is set and no `.env` file exists
- **THEN** no component connects to a database on the strength of a value held in the repository

### Requirement: Keep no credential in the repository

The repository SHALL NOT contain a database password, or a user and password pair, in any tracked file. A local `.env` file SHALL be excluded from version control. A committed `.env.example` SHALL list every variable with a placeholder value and SHALL say which are required and which are optional.

#### Scenario: The local file is not tracked

- **WHEN** a `.env` file is created in the repository root
- **THEN** version control does not offer it for commit

#### Scenario: The template carries no secret

- **WHEN** `.env.example` is read
- **THEN** every value in it is a placeholder, and a contributor can copy it to `.env` and fill it in

### Requirement: Load a local `.env` file, with the environment taking precedence

The system SHALL read a `.env` file at the repository root, if one exists, and treat its entries as environment variables. A variable already set in the real environment SHALL take precedence over the same variable in the file. The absence of a `.env` file SHALL NOT be an error by itself, so that a host that supplies the variables directly needs no file.

#### Scenario: A local developer configures with the file

- **WHEN** `.env` sets `PGHOST`, `PGUSER` and `PGDATABASE` and nothing is set in the shell
- **THEN** the system connects using those values from any working directory

#### Scenario: The real environment wins

- **WHEN** `.env` sets `PGHOST` to one value and the shell exports `PGHOST` with another
- **THEN** the system connects using the shell's value

#### Scenario: No file on a host that sets the variables

- **WHEN** there is no `.env` file and the three required variables are set in the real environment
- **THEN** the system connects using them, without error

### Requirement: Fail loudly when the database is not configured

`PGHOST`, `PGUSER` and `PGDATABASE` SHALL be required. `PGPASSWORD` and `PGPORT` SHALL be optional, so that passwordless authentication, a password held in a `~/.pgpass` file, and the default port all remain usable.

When any required variable is missing, the system SHALL raise an error before attempting a connection. The error SHALL name each missing variable and SHALL point the reader to `.env.example`. It SHALL NOT fall back to a default, to the local socket, or to the operating-system user.

#### Scenario: A required variable is missing

- **WHEN** `PGUSER` is not set anywhere and a component tries to connect
- **THEN** it fails with an error that names `PGUSER` and refers to `.env.example`, and no connection is attempted

#### Scenario: Several are missing

- **WHEN** none of the required variables is set
- **THEN** the single error names all three

#### Scenario: Optional variables may be absent

- **WHEN** `PGHOST`, `PGUSER` and `PGDATABASE` are set and `PGPASSWORD` and `PGPORT` are not
- **THEN** the configuration check passes and the connection is attempted

### Requirement: Fail loudly when the database is unreachable

When the configuration check passes and the connection fails, the system SHALL report the failure as an error, whether or not the optional variables were set. It SHALL NOT retry against another host, fall back to another configuration, or continue as though a connection had been made.

#### Scenario: Wrong host or refused connection

- **WHEN** the required variables are set to a host that does not answer
- **THEN** the component fails with the connection error

#### Scenario: Wrong password

- **WHEN** `PGPASSWORD` is set to a value the server rejects
- **THEN** the component fails with the authentication error, and does not retry without it

#### Scenario: Missing optional variables do not change the outcome

- **WHEN** the connection fails and `PGPASSWORD` and `PGPORT` are unset
- **THEN** it fails with the same kind of error as when they are set

### Requirement: The served application refuses to start unconfigured

The served application SHALL check the database configuration when it starts, and SHALL NOT start serving when a required variable is missing. A deployment that is misconfigured SHALL fail at start-up, not on the first request that reaches the database.

#### Scenario: Start-up without configuration

- **WHEN** the application is started with a required `PG*` variable missing
- **THEN** it exits with the configuration error and serves no request

#### Scenario: Start-up with configuration

- **WHEN** the application is started with the three required variables set
- **THEN** it starts and serves requests

### Requirement: Database tests fail rather than skip

A test that needs the database SHALL fail with an error when the database is unconfigured, and SHALL fail with an error when it is configured but unreachable. It SHALL NOT be skipped, so a run that exercised no database code cannot report success. Tests that need no database SHALL still run without one.

#### Scenario: Unconfigured test run

- **WHEN** the test suite is run with no database configuration
- **THEN** every test that needs the database errors with the configuration error, and the run does not pass

#### Scenario: Configured but unreachable test run

- **WHEN** the test suite is run with configuration naming a database that does not answer
- **THEN** every test that needs the database errors with the connection error, and the run does not pass

#### Scenario: Tests without a database

- **WHEN** the test suite is run with no database configuration
- **THEN** the tests that need no database still run and pass

### Requirement: Database credentials are the only credentials the system holds

The database password is the only credential the system holds, and it is used only to reach the system's own database. The sync SHALL still need no credential of any kind to read the source layers, and no database credential SHALL be sent to the source or to a viewer.

#### Scenario: The sync needs no source credential

- **WHEN** the scheduled sync runs with the database configured
- **THEN** it reads the source layers without any key, token or account

#### Scenario: No credential reaches a viewer

- **WHEN** any served page, API response or export is inspected
- **THEN** it contains no `PG*` value
