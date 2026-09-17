-- The mirror's schema. Applied by `src/mirror/db.py`, idempotently.
--
-- Three mirrored layers, the state the sync keeps about them, the record of every
-- sync attempt, the list history that replaces the git history of out/, and the
-- shared triage decisions. Geometry is JSONB: there is no PostGIS here, and census
-- containment stays the even-odd ray cast the pipeline already uses.

CREATE SCHEMA IF NOT EXISTS mirror;

SET search_path TO mirror, public;

-- The call. One row per source feature, keyed by the source's own ObjectId so a
-- re-read of a page is an upsert rather than a duplicate.
CREATE TABLE IF NOT EXISTS service_requests (
    object_id           bigint PRIMARY KEY,
    request_id          integer NOT NULL,
    date_initiated      timestamptz,
    date_closed         timestamptz,
    description         text,
    initiated_by        text,
    priority            text,
    address             text,
    community           text,
    district            text,
    request_category    text,
    resolution          text,
    latitude            double precision,
    longitude           double precision,
    status              text,
    dept_responsibility text,
    work_order          text,
    mirrored_at         timestamptz NOT NULL DEFAULT now()
);

-- Read by every per-request derivation (mirror.derive.load -> _service_request_rows,
-- task 5.5, design.md M11): `WHERE request_id = ANY(...) ORDER BY request_id`, once
-- per call to derive()/derive_all(), against whatever selection a violation type or
-- --canonical-type names. Audited against EXPLAIN (ANALYZE, BUFFERS) on the fully
-- loaded mirror (478,458 rows) for task 5.5 rather than assumed: this index is what
-- turns that lookup into an index scan already ordered by request_id (so no separate
-- sort is needed either), 9,834 rows in ~40ms warm. No further index was found
-- missing for the derivation's read path -- see the same task's measurements.
CREATE INDEX IF NOT EXISTS service_requests_request_id_idx
    ON service_requests (request_id);
CREATE INDEX IF NOT EXISTS service_requests_date_initiated_idx
    ON service_requests (date_initiated);
-- Doorway grouping reduces the address to upper case before keying on it.
CREATE INDEX IF NOT EXISTS service_requests_address_idx
    ON service_requests (upper(address));
CREATE INDEX IF NOT EXISTS service_requests_district_idx
    ON service_requests (district);

-- The custom fields, stored key-value exactly as published: 1,156,710 rows across
-- 88 distinct field names. Not pivoted on ingest — 88 names do not fit 7 columns,
-- and a pivoted store could not be counted against the service's own count query.
CREATE TABLE IF NOT EXISTS custom_fields (
    object_id          bigint PRIMARY KEY,
    request_id         integer NOT NULL,
    custom_field_id    integer,
    custom_field_name  text,
    custom_field_value text,
    mirrored_at        timestamptz NOT NULL DEFAULT now()
);

-- Read by the per-request derivation's parking_call_attributes fetch (task 5.5):
-- `WHERE request_id = ANY(...)` pushed down through that view's GROUP BY, so this
-- index -- not a table scan -- is what a canonical type's vehicle/outcome pivot
-- runs against. ~9,834 ids resolved to ~68,838 matching rows in ~50ms warm on the
-- fully loaded mirror (task 5.5's measurement).
CREATE INDEX IF NOT EXISTS custom_fields_request_id_idx
    ON custom_fields (request_id);
-- Selecting a violation type: name = 'Alleged Violation' AND value LIKE '%DRIVEWAY%',
-- or (canonical-type selection, task 4.15/4.16) name = 'Alleged Violation' AND value
-- = ANY(frozen label list). Both are read by mirror.derive.load on every per-request
-- derivation (task 5.5, design.md M11) -- audited by EXPLAIN against the fully loaded
-- mirror rather than assumed: this index turns the substring selection into an index
-- range scan on name narrowed to ~110,900 rows before the LIKE filter runs (~69ms
-- warm with two parallel workers -- a LIKE with a leading wildcard cannot use a btree
-- range on value itself, so filtering that subset in the executor is the ceiling, not
-- a gap this index could close), and the canonical-type selection into a bitmap index
-- scan covering both columns directly (~87ms warm for two labels/9,834 rows). No
-- additional index was found missing for either read path.
CREATE INDEX IF NOT EXISTS custom_fields_name_value_idx
    ON custom_fields (custom_field_name, custom_field_value);
-- The per-call pivot reads seven names by request.
CREATE INDEX IF NOT EXISTS custom_fields_name_request_idx
    ON custom_fields (custom_field_name, request_id);

-- Census dissemination areas. Rings are JSONB in the source's own [[[lon,lat],...]]
-- shape; the bounding box is stored alongside so a containment test can discard
-- almost every polygon without parsing it.
CREATE TABLE IF NOT EXISTS census_areas (
    object_id       bigint PRIMARY KEY,
    dauid           text UNIQUE,
    population      integer,
    dwellings       integer,
    usual_dwellings integer,
    area            double precision,
    pop_density     double precision,
    rep_lat         double precision,
    rep_lon         double precision,
    csd_name        text,
    cd_name         text,
    rings           jsonb NOT NULL,
    min_lon         double precision,
    min_lat         double precision,
    max_lon         double precision,
    max_lat         double precision,
    mirrored_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS census_areas_box_idx
    ON census_areas (min_lon, max_lon, min_lat, max_lat);

-- Per-layer sync state: the counts last reconciled and the clocks the application
-- reports. `watermark` is the highest object id held, recorded as an observation and
-- read by nothing: no pull filters on it. The source's ObjectId is reassigned on each
-- publish (design.md M2), so it says how big the version is, never where to resume.
CREATE TABLE IF NOT EXISTS layer_state (
    layer                  text PRIMARY KEY,
    watermark              bigint NOT NULL DEFAULT 0,
    source_count           bigint,
    stored_count           bigint,
    source_last_edit       timestamptz,
    full_load_completed_at timestamptz,
    last_attempt_at        timestamptz,
    last_attempt_ok        boolean,
    last_success_at        timestamptz,
    next_due_at            timestamptz,
    static                 boolean NOT NULL DEFAULT false
);

-- Resumability at page granularity. Each page's rows and its new offset are
-- committed together, so an interrupted load restarts at the first page that did
-- not commit rather than at zero.
-- A reload's staging fill is tracked here too, under `<layer>__staging`, together
-- with the published version it is being filled from: a fill resumed against a
-- version the source has since replaced restarts rather than mixing two of them.
CREATE TABLE IF NOT EXISTS load_progress (
    layer            text PRIMARY KEY,
    page_size        integer NOT NULL,
    source_last_edit timestamptz,
    next_offset     bigint NOT NULL DEFAULT 0,
    pages_completed integer NOT NULL DEFAULT 0,
    rows_loaded     bigint NOT NULL DEFAULT 0,
    source_count    bigint,
    started_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);

-- Applied to stores created before these two changes landed: `IF NOT EXISTS` on a
-- table does not add a column to one that already exists, and the partial index that
-- served the retired open-set refetch is not removed by omitting it here.
ALTER TABLE load_progress ADD COLUMN IF NOT EXISTS source_last_edit timestamptz;
DROP INDEX IF EXISTS service_requests_open_idx;

-- Every attempt, successful or not. A failure is a row, not an absence of one.
CREATE TABLE IF NOT EXISTS sync_runs (
    id                bigserial PRIMARY KEY,
    kind              text NOT NULL,
    layer             text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    ok                boolean,
    watermark_reached bigint,
    rows_inserted     bigint NOT NULL DEFAULT 0,
    rows_updated      bigint NOT NULL DEFAULT 0,
    pages_fetched     integer NOT NULL DEFAULT 0,
    source_last_edit  timestamptz,
    source_count      bigint,
    stored_count      bigint,
    reconciled        boolean,
    error             text
);

CREATE INDEX IF NOT EXISTS sync_runs_layer_started_idx
    ON sync_runs (layer, started_at DESC);
CREATE INDEX IF NOT EXISTS sync_runs_success_idx
    ON sync_runs (layer, finished_at DESC) WHERE ok;

-- Anomalies a sync notices but cannot resolve: a stored count that disagrees with
-- the service's own, a source that published again while a reload was reading it.
-- Reported, never silently resolved.
CREATE TABLE IF NOT EXISTS sync_anomalies (
    id          bigserial PRIMARY KEY,
    sync_run_id bigint REFERENCES sync_runs (id),
    layer       text,
    kind        text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    noticed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sync_anomalies_noticed_idx
    ON sync_anomalies (noticed_at DESC);

-- Each published version's identity, retained before the version is replaced, so the
-- question design.md M2a leaves open can be answered from observation rather than
-- reopened with new instrumentation: does a record keep its ObjectId across a
-- publish? A version row is written for the version going out and for the one coming
-- in, and (layer, source_last_edit) identifies a version, so re-recording one is a
-- no-op rather than a duplicate.
CREATE TABLE IF NOT EXISTS layer_versions (
    id                bigserial PRIMARY KEY,
    layer             text NOT NULL,
    source_last_edit  timestamptz,
    row_count         bigint,
    highest_object_id bigint,
    sample_size       integer,
    sample_stride     integer,
    sync_run_id       bigint REFERENCES sync_runs (id),
    note              text,
    retained_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (layer, source_last_edit)
);

CREATE INDEX IF NOT EXISTS layer_versions_layer_idx
    ON layer_versions (layer, retained_at DESC);

-- The sample that makes renumbering detectable: business key to object id, for a
-- fixed residue class of the business key, so two versions sample the same records
-- rather than two disjoint thousands. A few thousand rows per version, against the
-- 478,458 the full mapping would cost, settles a yes-or-no question.
CREATE TABLE IF NOT EXISTS layer_version_samples (
    version_id   bigint NOT NULL REFERENCES layer_versions (id) ON DELETE CASCADE,
    business_key text NOT NULL,
    object_id    bigint NOT NULL,
    PRIMARY KEY (version_id, business_key)
);

CREATE INDEX IF NOT EXISTS layer_version_samples_key_idx
    ON layer_version_samples (business_key);

-- The lists each sync produced, retained so that which doorway left the list and
-- when survives the removal of the scheduled job that was recording it in git.
CREATE TABLE IF NOT EXISTS list_snapshots (
    id                     bigserial PRIMARY KEY,
    sync_run_id            bigint REFERENCES sync_runs (id),
    violation_type         text NOT NULL,
    parameters             jsonb NOT NULL DEFAULT '{}'::jsonb,
    derived_at             timestamptz NOT NULL DEFAULT now(),
    mirror_last_success_at timestamptz,
    latest_call_date       date
);

CREATE INDEX IF NOT EXISTS list_snapshots_type_derived_idx
    ON list_snapshots (violation_type, derived_at DESC);

-- The published version of each tracked layer at the moment this snapshot was
-- derived -- {"service_requests": "<source_last_edit>", ...} -- so "what was this
-- derived from" survives independently of `parameters` (which carries the
-- thresholds, not the data version) and of `sync_run_id` (one run against one
-- layer; a derivation reads all three). Added after the three tables above via
-- ALTER ... ADD COLUMN IF NOT EXISTS, so apply_schema stays safe to re-run
-- against a store that already has list_snapshots.
ALTER TABLE list_snapshots
    ADD COLUMN IF NOT EXISTS mirror_version jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS doorway_list_history (
    snapshot_id       bigint NOT NULL REFERENCES list_snapshots (id) ON DELETE CASCADE,
    address           text NOT NULL,
    rank              integer,
    block             text,
    district          text,
    calls_recent      integer,
    calls_total       integer,
    tows              integer,
    vehicles_seen     integer,
    vehicles_distinct integer,
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, address)
);

CREATE INDEX IF NOT EXISTS doorway_list_history_address_idx
    ON doorway_list_history (address);

CREATE TABLE IF NOT EXISTS block_list_history (
    snapshot_id  bigint NOT NULL REFERENCES list_snapshots (id) ON DELETE CASCADE,
    block        text NOT NULL,
    rank         integer,
    streets      text,
    district     text,
    doorways     integer,
    calls_recent integer,
    calls_total  integer,
    tows         integer,
    dwellings    integer,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, block)
);

CREATE INDEX IF NOT EXISTS block_list_history_block_idx
    ON block_list_history (block);

-- Filter-independent figures per canonical type -- per-type conclusions (recurrence,
-- the tow effect bound, vehicle uniqueness), response time and call denominators --
-- computed once per published mirror version rather than once per request (task 5.7,
-- design.md M12: "figures that do not depend on filters are computed when the mirror
-- reloads"). `mirror_version` carries the same version identity `list_snapshots.
-- mirror_version` already does (`history._mirror_version`'s {layer: source_last_edit}
-- map), so a row states plainly which published version of the mirror it was
-- computed from. `parameters` folds in the bootstrap seed and iteration count
-- alongside the derivation thresholds, so two runs under different parameters (or a
-- coarser bootstrap) land as distinct rows rather than overwriting one another, and
-- the uniqueness constraint below -- on (mirror_version, canonical_type, parameters)
-- together -- is what makes re-running the compute-and-store command against a
-- version it has already covered a no-op rather than a duplicate.
CREATE TABLE IF NOT EXISTS type_figures (
    id             bigserial PRIMARY KEY,
    sync_run_id    bigint REFERENCES sync_runs (id),
    canonical_type text NOT NULL,
    mirror_version jsonb NOT NULL,
    parameters     jsonb NOT NULL DEFAULT '{}'::jsonb,
    figures        jsonb NOT NULL,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mirror_version, canonical_type, parameters)
);

CREATE INDEX IF NOT EXISTS type_figures_type_computed_idx
    ON type_figures (canonical_type, computed_at DESC);

-- Shared triage. A role identifies; it does not authenticate.
CREATE TABLE IF NOT EXISTS triage_decisions (
    id             bigserial PRIMARY KEY,
    violation_type text NOT NULL,
    scope          text NOT NULL CHECK (scope IN ('doorway', 'block')),
    item_key       text NOT NULL,
    decision       text NOT NULL,
    note           text,
    role           text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (violation_type, scope, item_key)
);

CREATE INDEX IF NOT EXISTS triage_decisions_type_scope_idx
    ON triage_decisions (violation_type, scope);

-- The seven parking attributes, one row per request that carries an alleged
-- violation. This is a view over the key-value rows, not a stored table: the
-- store keeps the published shape, and the pivot is derived from it on read.
CREATE OR REPLACE VIEW parking_call_attributes AS
SELECT
    request_id,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Alleged Violation')  AS alleged_violation,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Property Ownership') AS property_ownership,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Vehicle Was Towed')  AS vehicle_was_towed,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Vehicle Make')       AS vehicle_make,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Vehicle Model')      AS vehicle_model,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Vehicle Colour')     AS vehicle_colour,
    max(custom_field_value) FILTER (WHERE custom_field_name = 'Vehicle Province')   AS vehicle_province,
    count(*) FILTER (WHERE custom_field_name = 'Alleged Violation')                 AS alleged_violation_rows
FROM custom_fields
WHERE custom_field_name IN (
    'Alleged Violation', 'Property Ownership', 'Vehicle Was Towed',
    'Vehicle Make', 'Vehicle Model', 'Vehicle Colour', 'Vehicle Province'
)
GROUP BY request_id
HAVING count(*) FILTER (WHERE custom_field_name = 'Alleged Violation') > 0;
