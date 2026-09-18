# Docs index

One line per file.

## problem-selection

| File | What it is for | Status | Date |
|------|----------------|--------|------|
| [problem-selection/five-problems-research.md](problem-selection/five-problems-research.md) | The research behind the pick. Tests all five Impact Lab problems against live HRM data and HRM primary documents. | Final | 2026-09-12 |
| [problem-selection/decisions.md](problem-selection/decisions.md) | Decisions about which problem to build, who decided, and what is still open. | Live | 2026-09-12 |

## parking-hotspots

| File | What it is for | Status | Date |
|------|----------------|--------|------|
| [parking-hotspots/data-sources.md](parking-hotspots/data-sources.md) | The HRM datasets the product reads, their fields, and their limits. Includes the Commuter Permit Parking Streets test. | Final | 2026-09-12 |
| [parking-hotspots/product.md](parking-hotspots/product.md) | What the product is, who uses it, and what is real against what is stubbed. | Final | 2026-09-12 |
| [parking-hotspots/decisions.md](parking-hotspots/decisions.md) | Decisions about the product design and the open items. | Live | 2026-09-12 |

## The product

The board is a served application (decision M5 of the `mirror-hrm-data-and-host-app` change), not a file. A list leaves it only through its
two export routes, described in [the README](../README.md#where-a-list-leaves-the-application).

| File | What it is |
|------|------------|
| `web/app/index.html` | The board: the page the server sends, which fetches its data from the JSON API. |
| `web/map-network.json` | HRM's street network, fetched by that page as `/map-network.json`. |
| `src/app/server.py` | The server: the page, the JSON API, and the two export routes. |

## Retired, not docs

Nothing in this repository generates committed output any more, and the output the retired
generator left behind has been deleted. The `out/` directory and its five files are gone; they
were historical snapshots, not the board and not a current list, so do not read them as today's
numbers. The last commit that still contains them is `b04731c`; read any of them with
`git show b04731c:<path>`.

| File (deleted) | What it was |
|----------------|-------------|
| `out/triage-board.html` | The last standalone board the retired generator wrote, with its data embedded. Not the board. |
| `out/watchlist.csv`, `out/watchlist.md` | The last doorway list and brief the retired generator wrote. |
| `out/blocks.csv`, `out/blocks.md` | The last block list and brief the retired generator wrote. |

`web/template.html`, the source the standalone board was built from, was retired and deleted in
the same way. It too is last present in `b04731c`; read it with
`git show b04731c:web/template.html`.
