# Task dispatch plan — mirror-hrm-data-and-host-app (sections 3–9)

This is a **temporary working document**, not an OpenSpec artifact — delete it once the change is implemented or archived. It exists to let an orchestrating agent assign tasks from `openspec/changes/mirror-hrm-data-and-host-app/tasks.md` (sections 3 onward; sections 1–2 are already complete) to a pool of worker agents concurrently instead of serially. Each tier below is a dispatch pool: every task in a tier can be started in parallel once its listed dependencies are satisfied. Tasks marked 🔴 BOTTLENECK gate a large fan-out of downstream work and should be staffed/prioritized first within their tier. Full task text lives in `tasks.md`; this file only carries IDs, short labels, and dependency edges.

## Tier 0 — dispatch immediately, no prerequisites (parallel)

| ID | Task | Unblocks |
|---|---|---|
| 3.1 | Record every sync attempt | 3.2, 3.3 |
| 3.5 | Compare per-layer stored counts vs service | — |
| **3.6** | **Row-level reconciliation of the mirror** | **🔴 BOTTLENECK — blocks all of Tier 3 (section 4)** |
| 4.6 | DST-aware timezone conversion in `src/hotspots.py` | 4.6a |
| 4.15 | Freeze canonical violation-type list (live query) | 4.16 |
| 8.8 | Remove unsourced "weekly-refreshed" claim | — |

## Tier 1 — after their Tier-0 prerequisite (parallel)

| ID | Task | Waits on |
|---|---|---|
| 3.2 | Expose last attempt / last success | 3.1 |
| 3.3 | Expose four clocks | 3.1 |
| 4.6a | Verify DST fix doesn't shift doorway list | 4.6 |

## Tier 2

| ID | Task | Waits on |
|---|---|---|
| 3.4 | Report mirror-behind-source vs source-not-published | 3.3 |
| 8.1a | Re-derive hour-of-day/channel figures under corrected TZ | 4.6a |

## 🔴 BOTTLENECK — Tier 3

| ID | Task | Why it's a bottleneck |
|---|---|---|
| **4.1** | **Read selection/join/outcome/vehicle/address/doorway/census from the mirror** | Waits only on 3.6. Unblocks 4.2, 4.3, 4.5, 4.7, 4.8, 4.10, 4.13, 4.16, 8.6 — the single biggest fan-out in the whole change |

## Tier 4 — after 4.1 (parallel; Tier 0/1/2 stragglers may still be finishing)

| ID | Task | Waits on |
|---|---|---|
| 4.4 | Census read from the mirror — dispatch together with 4.1, in the new derivation module | 4.1 |
| 4.2 | Remove live-fallback path | 4.1 |
| 4.3 | Report derivation-on-unreconciled-mirror | 4.1 |
| 4.5 | Preserve census containment correctness | 4.1 |
| 4.8 | Automated coverage for pure functions | 4.1 |
| 4.7 | Carry derivation/sync/last-call times onto output | 4.1, 3.3 |
| 4.10 | Compute recurrence split by tow status | 4.1 |
| 4.13 | Compute median elapsed time (response-time figure) | 4.1 |
| 8.6 | Retain doorway/block lists per sync in store | 4.1 |
| **4.16** | **Derive every canonical type from one pass over mirror** | 4.1, 4.15 — **BOTTLENECK for section 4c** (unblocks 4.17, 4.18, 4.19, and later 5.2) |

## Tier 5

| ID | Task | Waits on |
|---|---|---|
| 4.11 | Report effect size the tow comparison rules out | 4.10 |
| 4.14 | Reconcile call denominators | 4.10, 4.13 |
| 4.17 | Recurrence/tow/vehicle uniqueness per type | 4.16 |
| 4.18 | Check `No Parking Sign` against string-reduction concern | 4.16 |
| 4.19 | Measure all-types derivation time | 4.16 |

## Tier 6

| ID | Task | Waits on |
|---|---|---|
| 4.12 | State tow-comparison observational caveat | 4.11 |
| 8.1 | Replace hand-written figures with generated values | 4.10, 4.11, 4.12, 4.13, 4.14 |

## 🔴 BOTTLENECK — Tier 7

| ID | Task | Why it's a bottleneck |
|---|---|---|
| **4.9** | **Figure-level reconciliation (`src/hotspots.py` vs mirror derivation, Driveway)** | Waits on 4.2, 4.3, 4.5, 4.6, 4.6a, 4.7, 4.8. Gates the entire application-serving migration — nothing in sections 5–7 may build against derived tables before this passes |

## 🔴 BOTTLENECK — Tier 8

| ID | Task | Why it's a bottleneck |
|---|---|---|
| **5.1** | **Serve doorway/block/map lists over HTTP** | Waits only on 4.9. Unblocks 5.2, 5.4, 5.5, 5.6, 6.1, 7.1, 7.3, 7.4a, 7.6, 8.3 — second-biggest fan-out in the change |

## Tier 9 — after 5.1 (large parallel pool)

| ID | Task | Waits on |
|---|---|---|
| **5.2** | Route per canonical violation type | 5.1, 4.16, 4.17, 4.18, 4.19 — **BOTTLENECK, unblocks 5.3, 7.7** |
| 5.4 | Serve street network as cacheable asset | 5.1 |
| **5.5** | Compute derived results per request against indexed mirror | 5.1 — **BOTTLENECK, unblocks 5.5a, 5.5b** |
| 5.6 | Preserve light/dark rendering | 5.1 |
| 5.8 | Serve stored filter-independent figures | 5.1, 5.7 |
| **6.1** | Persist triage decision server-side | 5.1 — **BOTTLENECK, unblocks 6.2, 6.3, 6.4, 6.6** |
| 7.1 | Prominent freshness message | 5.1, 3.3, 3.4 |
| 7.3 | Remove undated "regenerated live" claim | 5.1 |
| 7.4a | Warn when most recent call is materially stale | 5.1, 3.3 |
| 7.6 | Explain block neighbour count | 5.1 |
| 8.3 | Qualify "No keys and no install" to the viewer | 5.1 |

## Tier 10

| ID | Task | Waits on |
|---|---|---|
| 5.3 | Report untracked type as untracked | 5.2 |
| 5.5a | District/threshold/window controls | 5.5 |
| 5.5b | Expose recency window as a parameter | 5.5 |
| 6.2 | Verify decisions survive restart | 6.1 |
| 6.3 | Remove browser-storage fallback | 6.1 |
| 6.4 | State plainly when persist fails | 6.1 |
| 6.6 | Ask for role before decision | 6.1 |
| 7.2 | Attribute lag to owning system | 7.1, 3.4 |

## Tier 11

| ID | Task | Waits on |
|---|---|---|
| 5.5c | Distinctly label recency vs recurrence windows | 5.5a, 5.5b |
| 5.5e | Report empty filter result as empty | 5.5a |
| 6.5 | Attribute decision to role + time | 6.1, 6.6 |
| 6.7 | State roles identify, not authenticate | 6.6 |
| 7.2a | Next-update as overdue state machine | 7.2 |
| 7.4 | State recency window anchor/length on filtered list | 5.5b |
| 8.2 | Correct hosted-copy claim | 5.1, 6.1 |

## Tier 12

| ID | Task | Waits on |
|---|---|---|
| 5.5d | State filter values in effect on view/export | 5.5c |
| 7.2b | State "next update unknown" when history insufficient | 7.2a |
| 7.5 | Carry interpretation limits into served views | 5.1, 5.2, 4.11, 4.12, 4.16–4.19 |

## 🔴 BOTTLENECK — Tier 13

| ID | Task | Why it's a bottleneck |
|---|---|---|
| **7.7** | **Export doorway/block lists + brief** | Waits on 5.2, 5.5d, 7.1. Unblocks 7.7a, 7.8, 8.4, and critically **8.7** (must not turn off `out/` generation before this replacement exists) |

## Tier 14

| ID | Task | Waits on |
|---|---|---|
| 7.7a | Name canonical type on export/view | 7.7, 4.15 |
| 8.4 | Replace "open the board" instructions with URL | 5.1, 7.7 |
| 8.7 | Stop generating committed output to `out/` | 5.1, 7.7 |

## Tier 15

| ID | Task | Waits on |
|---|---|---|
| 7.8 | Verify view and export agree on every count | 7.7 |
| 8.5 | Record decisions.md narrative | 8.1, 8.1a, 8.2, 8.3, 8.4, 8.6, 8.7, 8.8 |

## Tier 16 — section 9 verification (parallel; final tier)

| ID | Task | Waits on |
|---|---|---|
| 9.1 | App serves with HRM unreachable, states freshness | 5.1, 7.1 |
| 9.2 | Overdue sync reads as overdue | 7.2a |
| 9.3 | No 311 call-detail value anywhere in delivered system | 5.2, 7.5, 7.7 |
| 9.4 | Driveway figures match pre-change run post-cutover | 4.9, 5.1 |
| 9.5 | Board and exports agree on every count, incl. 4b figures | 7.8, 4.10–4.14 |
| 9.6 | Two viewers/roles share triage, see attribution | 6.1–6.7 |
| 9.7 | No view claims wrong RESOLUTION/hour-of-day finding | 4.6, 4.6a, 7.5 |
| 9.8 | No type inherits Driveway's conclusion without own evidence | 4.17, 7.5, 7.7a |

---

## Dependency map (flat, for building the DAG programmatically)

```
3.1: []
3.2: [3.1]
3.3: [3.1]
3.4: [3.3]
3.5: []
3.6: []                                   # BOTTLENECK
4.1: [3.6]                                # BOTTLENECK
4.2: [4.1]
4.3: [4.1]
4.4: [4.1]                                # dispatched with 4.1
4.5: [4.1]
4.6: []
4.6a: [4.6]
4.7: [4.1, 3.3]
4.8: [4.1]
4.9: [4.2, 4.3, 4.5, 4.6, 4.6a, 4.7, 4.8]  # BOTTLENECK
4.10: [4.1]
4.11: [4.10]
4.12: [4.11]
4.13: [4.1]
4.14: [4.10, 4.13]
4.15: []
4.16: [4.1, 4.15]                         # BOTTLENECK
4.17: [4.16]
4.18: [4.16]
4.19: [4.16]
5.1: [4.9]                                # BOTTLENECK
5.2: [5.1, 4.16, 4.17, 4.18, 4.19]         # BOTTLENECK
5.3: [5.2]
5.4: [5.1]
5.5: [5.1]                                # BOTTLENECK
5.5a: [5.5]
5.5b: [5.5]
5.5c: [5.5a, 5.5b]
5.5d: [5.5c]
5.5e: [5.5a]
5.6: [5.1]
5.7: [4.17, 8.6]                          # added with M12; ready now
5.8: [5.1, 5.7]
6.1: [5.1]                                # BOTTLENECK
6.2: [6.1]
6.3: [6.1]
6.4: [6.1]
6.5: [6.1, 6.6]
6.6: [6.1]
6.7: [6.6]
7.1: [5.1, 3.3, 3.4]
7.2: [7.1, 3.4]
7.2a: [7.2]
7.2b: [7.2a]
7.3: [5.1]
7.4: [5.5b]
7.4a: [5.1, 3.3]
7.5: [5.1, 5.2, 4.11, 4.12, 4.16, 4.17, 4.18, 4.19]
7.6: [5.1]
7.7: [5.2, 5.5d, 7.1]                     # BOTTLENECK
7.7a: [7.7, 4.15]
7.8: [7.7]
8.1: [4.10, 4.11, 4.12, 4.13, 4.14]
8.1a: [4.6a]
8.2: [5.1, 6.1]
8.3: [5.1]
8.4: [5.1, 7.7]
8.5: [8.1, 8.1a, 8.2, 8.3, 8.4, 8.6, 8.7, 8.8]
8.6: [4.1]
8.7: [5.1, 7.7]
8.8: []
9.1: [5.1, 7.1]
9.2: [7.2a]
9.3: [5.2, 7.5, 7.7]
9.4: [4.9, 5.1]
9.5: [7.8, 4.10, 4.11, 4.12, 4.13, 4.14]
9.6: [6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7]
9.7: [4.6, 4.6a, 7.5]
9.8: [4.17, 7.5, 7.7a]
```
