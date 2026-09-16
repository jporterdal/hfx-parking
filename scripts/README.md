# scripts/

Re-runnable measurement scripts kept from session scratchwork. Not part of
the shipped `src/` code or the test suite (pyproject's `testpaths` is
`tests`, so pytest never collects these). Each script has a full docstring
with purpose, data source, and how to run it; this is just an index.

- `hour_of_day_figures.py` -- hour-of-day / channel figures for the Driveway
  population (task 8.1), old fixed-UTC-3 vs new DST-aware conversion. Data
  source: **live** HRM open-data endpoints (network, ~3-5 min).
- `address_spread.py` -- address-collision / spread measurement for the
  reduced-address doorway key (task 4.18), e.g. No Parking Sign vs Blocking
  Driveway. Data source: local mirror, read-only (~1s).
- `derivation_timing.py` -- all-types derivation timing, SQL vs Python
  breakdown (task 4.19). Data source: local mirror, read-only (~10s for 5 runs).
- `snapshot_storage.py` -- on-disk size of one all-types list snapshot, for
  the snapshot-retention question. Reads the real mirror, writes one snapshot
  into a throwaway schema (`mirror_throwaway_snapshot_storage`) that it drops
  before exiting (~few seconds).
