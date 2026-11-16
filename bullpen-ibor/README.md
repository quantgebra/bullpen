# bullpen-ibor

Part of [BullPen](../README.md). The Gen 3 IBOR (investment book of record): positions are projections over an
append-only ledger of events in Postgres (schema `ibor`). Nothing but events (and the source documents that cause
them) is stored. The design is in [`../docs/architecture.md`](../docs/architecture.md) §4; the executable
specification is the user guide under `tests/bullpen_ibor/`.

## Running

```sh
poetry install
poetry run pytest -q          # starts postgres:17-alpine with testcontainers; needs Docker
```

## Model

| Table | Kind | Holds |
|---|---|---|
| `trades` | versioned (knowledge range) | allocated trades as received; stable `trade_id`, one row per version |
| `events` | append-only | signed `delta_quantity` of a traded instrument, in a book, for a portfolio instrument |

A book is the `(fund_id, mandate_id, account_id)` triple of fund-master ids, stored on every trade and event; there is
no book table. `(fund_id, mandate_id)` is a sleeve. There are no foreign keys to the fund master: the command handler
checks that a book fits before booking.

Every event carries `trade_date` (its session), `settlement_date`, `effective_datetime` (when it happened: a trade's
execution time), `known_at_datetime` (when we learned it), its source (`source_type`, `source_row_id`:
the version of the document that caused it), `command_id`, and `reverses_event_id` for compensating events. Cash is an
instrument quantity. Instruments are securities-master ids; this package imports neither master.

## Reading positions

```python
position.get_book_positions(session, fund_id, mandate_id, account_id, position_date, known_at_datetime, PositionView.TRADED)
position.get_sleeve_positions(...)    # (fund_id, mandate_id), over its accounts
position.get_account_positions(...)   # by traded instrument: what the broker reports
position.get_fund_positions(...)
```

`TRADED` counts an event from its trade date, `SETTLED` from its settlement date; only events known at
`known_at_datetime` count, so earlier runs are reproducible.

## User guide

| Guide | Teaches |
|---|---|
| 01 booking and positions | books, booking a trade, sources, traded and settled views, late trades, roll-ups |
| 02 corrections | amendments and busts via reversals, restatements, reproducible runs, execution time |

## Corrections

`trade.with_new_terms(...)` amends and `trade.cancel(...)` busts. Either stops believing the version and reverses its
events; an amendment's new version books its own. `Event.find_learned_between(...)` returns what changed between two
runs.

## Next

Synthetic lineage against research weights, cash (fees, transfers, ladder), corporate-action application, lots and
realized P&L, intraday positions.
