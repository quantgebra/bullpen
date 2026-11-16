# BullPen

BullPen is an investment book of record and middle-office platform for **asset managers, family offices and
independent traders**. It keeps track of what you can trade, who owns what, and what you hold, and it can always tell
you what it believed at any moment in the past.

> Status: early development. The securities master, the fund master and the first two slices of the IBOR are
> implemented and tested; the REST server and client are scaffolds. Nothing here is production-ready yet.

## What it does

- **Securities master**: exchanges, equities, futures roots and contracts, synthetic instruments (constant-maturity
  futures, spreads, straddles, research names), vendor symbologies, trading calendars, quote conventions and
  corporate-action definitions.
- **Fund master**: funds, mandates (strategies), sleeves (a fund running a mandate), brokers and accounts.
- **IBOR** (investment book of record): an append-only ledger of position changes. Positions in the traded and
  settled views are sums over it, broken down by fund, mandate and account, and traced from each traded security back
  to the research position it serves. Trade amendments and busts are reversals, never edits.

Planned: cash and fees, corporate-action processing, tax lots and realized P&L, market data (prices and exchange
rates), an OMS, NAV and investor accounting, reconciliation, a UI.

## Principles

- **Reproducible by construction.** Reference data is bi-temporal: every fact has when it was true and when we
  believed it. The ledger is append-only, and corrections are new events. A report run last Tuesday can be re-run
  today and give last Tuesday's answer, and the difference to today's answer is explainable event by event.
- **Lineage to research.** A mandate's research names map to synthetic instruments whose legs are the contracts
  actually traded, so every position traces back to the research weight it implements, across rolls.
- **Postgres only.** No Redis, no message broker: writes are commands in a Postgres command queue, applied one
  transaction at a time.
- **Hard boundaries.** Each package owns its own Postgres schema and refers to other packages' entities by id only.

## Packages

| Package | Purpose | Status |
|---|---|---|
| [`bullpen-common`](bullpen-common/README.md) | wire DTOs and shared types | scaffold |
| [`bullpen-temporal`](bullpen-temporal/README.md) | shared bi-temporal persistence (`AsOf`, `BitemporalRow`) | in use |
| [`bullpen-sec-master`](bullpen-sec-master/README.md) | securities master | implemented, 70 tests |
| [`bullpen-fund-master`](bullpen-fund-master/README.md) | funds, mandates, sleeves, brokers, accounts | implemented, 9 tests |
| [`bullpen-ibor`](bullpen-ibor/README.md) | event ledger and positions | slices 1-2, 18 tests |
| [`bullpen-server`](bullpen-server/README.md) | REST API and command-queue worker | scaffold |
| [`bullpen-client`](bullpen-client/README.md) | typed HTTP client | scaffold |

## Getting started

Requirements: Python 3.11-3.14, [Poetry](https://python-poetry.org/), and Docker (the tests start a throwaway
PostgreSQL 17 container with testcontainers).

Each package is an independent Poetry project. To run a package's tests:

```sh
cd bullpen-sec-master
poetry install
poetry run pytest -q
```

Each package's tests are written as a numbered user guide (`tests/<package>/test_guide_01_...`): reading them in order
is the best introduction to that part of the system.

## Documentation

- [`docs/architecture.md`](docs/architecture.md): the design, decisions taken, and open questions.
- [`AGENTS.md`](AGENTS.md): code style rules, binding for all code in this repository.
