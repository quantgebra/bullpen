# bullpen-sec-master

The bi-temporal securities master for bullpen: exchanges, instruments, futures roots, synthetic instruments, vendor
and research symbologies, trading calendars, quote conventions and corporate-action definitions. It is a library over
Postgres (schema `sec_master`) using SQLAlchemy 2.0; the IBOR and the server use it through a single SQLAlchemy session
per command.

The executable specification is the user guide under `tests/bullpen_sec_master/` (`test_guide_01_...` to
`test_guide_10_...`): each file teaches one part of the system, in order. The wider architecture is in
[`../docs/architecture.md`](../docs/architecture.md); code style is in [`../AGENTS.md`](../AGENTS.md).

## Running

```sh
poetry install
poetry run pytest -q          # starts postgres:17-alpine with testcontainers; needs Docker
poetry run black -l 120 src tests
```

`schema.create_all(engine)` creates the `btree_gist` extension, the `sec_master` schema and every table.

## Three kinds of row

Everything in the package is one of three shapes, and knowing which one a class is tells you how to read and write it.

| Kind | Time axes | Classes |
|---|---|---|
| **Plain** | none | `Exchange`, `Symbology`, `TradingCalendar` |
| **Bi-temporal fact** | `effective_range` (DATERANGE) + `knowledge_range` (TSTZRANGE) | `Instrument` and subclasses, `InstrumentSyntheticLeg`, `FuturesRoot`, `SymbolFor*`, `QuoteConventionFor*` |
| **Knowledge-only event** | `knowledge_range` only | `TradingCalendarException`, `CorporateAction` (+ `CorporateActionOutcome`) |

- **Effective** is when a fact was true in the world ("it was called Facebook until 2022-06-08"); **knowledge** is
  when we believed it ("we learned of the rename on 2022-06-10"). Both are half-open `[)`.
- **Events** (a holiday, a dividend) are about their own dates, so an effective range would mean nothing; only their
  knowledge varies, because they are announced, amended and cancelled.
- **Rows are never edited** except to close `knowledge_range`. A change closes the believed rows and appends
  replacements, so "what did we believe about date D at time K?" always has an answer, and an order run is
  reproducible.
- **Overlap is impossible:** each table has a GiST `EXCLUDE` constraint over its key and both ranges.
- **Bi-temporal facts share one implementation:** the `BitemporalRow` mixin from `bullpen-temporal` (row id, ranges,
  as-of finders, `end`, supersede logic).

## Stable keys, row ids, no foreign keys to entities

`row_id` is a row's primary key. `instrument_id`, `futures_root_id` and `corporate_action_id` are **stable keys**,
allocated from sequences and repeated on every row of the entity. A stable key is not unique, so other tables refer to
it without a foreign key and resolve it with an as-of lookup. What an identity table would have enforced is enforced by
constraints instead (e.g. an instrument's type is immutable: `EXCLUDE (instrument_id WITH =, instrument_type WITH <>)`).
Plain rows keep ordinary foreign keys.

## Reading: `AsOf`

Bi-temporal reads take one `AsOf(effective_date, known_at_datetime)` from the shared `bullpen-temporal` package:

```python
as_of = as_of_module.AsOf(date(2026, 11, 13), datetime(2026, 11, 15, tzinfo=timezone.utc))
contract = Instrument.find_as_of(session, instrument_id, as_of)   # returns the concrete subclass
root = contract.get_root(session)                                  # read at the same AsOf, without being told
```

The `AsOf` is used as SQLAlchemy's `identity_token`, so one database row read at two `AsOf`s is two Python objects in
one session, and every row remembers what it was read at (`bullpen_temporal.as_of.get_as_of_from_row(row)`). Traversals
(`contract.get_root`, `synthetic.get_legs`, `symbol_row.get_target`) reuse it, which keeps one session, one commit or
rollback, per command however many dates it looks at. Rows you just wrote were not read as of anything: re-read them
before traversing. Plain rows are fetched with `session.get`, never through relationships from bi-temporal rows, so they
keep a single identity.

Knowledge-only events are read with an explicit date plus `known_at_datetime`, since an `AsOf`'s effective date would
be ignored.

## Writing

- `create(...)` allocates a new stable key. Bi-temporal creates take `effective_from_date` and `known_at_datetime`.
- A change is `with_new_<field>(session, effective_from_date, known_at_datetime, value)` on the latest believed row
  (`with_new_name`, `with_new_tick_size`, `with_new_legs`, `with_new_factors`, ...). It supersedes that row and returns
  the replacement; calling it on a stale row raises rather than silently losing later facts.
- `end(...)` stops a symbol or convention from a date on. Events have `with_new_dates`, `with_new_outcomes`, `cancel`,
  `record_holiday`, `record_special_session` and `remove_exception`.
- `command_id` columns are reserved for the command queue that will drive all writes.

## The model, part by part

**Exchanges and symbologies (guides 1-3).** An `Exchange` is identified by its MIC. A `Symbology` (IBKR, BBG, CSI) is a
naming system with an optional parent and an optional futures contract template. One symbology names exchanges,
futures roots and instruments alike, and stores only the symbols that differ from the target's canonical name (MIC for
exchanges, `name` for roots; instruments have none) or from its parent's. Symbols live in `symbols_for_exchanges`,
`symbols_for_futures_roots` and `symbols_for_instruments`; the operations are stateless functions in
`symbology_for_exchange`, `symbology_for_futures_root` and `symbology_for_instrument` (`create_symbol`, `get_symbol`,
`resolve_target`). Inbound resolution rejects an inherited symbol that a more specific symbology renamed. Futures
contract symbols are derived from the root's symbol plus the symbology's template (`{root}{month_code}{y}`) unless an
explicit symbol exists.

**Instruments (guides 2, 4).** One `instruments` table for every type, with type-specific facts in joined tables keyed
by `row_id` (`instrument_futures_contracts`), so they share the row's ranges. Subclasses: `InstrumentEquity`,
`InstrumentFuturesContract`, `InstrumentSynthetic`.

**Futures roots (guide 4).** `FuturesRoot` (CL, ES) is its own bi-temporal concept holding exchange, currency,
multiplier, tick size, settlement type and an optional calendar override. Tick value is derived. Contracts reference a
root by `futures_root_id` and read it at their own `AsOf`, so a root correction reaches every contract. A root is not an
instrument: it names a market, not something that can be held.

**Synthetic instruments (guides 5, 6).** `InstrumentSynthetic` is an instrument no broker can hold: VIX30, a calendar
spread, a straddle, a mandate's research name. It has bi-temporal legs `(leg instrument, signed weight)`, stored as
received and changed with `with_new_legs` (optionally over a bounded window). A leg is never synthetic, so
decomposition is one step. In the IBOR the synthetic is the portfolio security and its legs are the traded securities.
Each mandate has its own symbology mapping research names ("CL") to that mandate's synthetic; a roll is only a leg
change, so the position stays continuous. Exposure to a futures market is always through a synthetic that labels its
roll policy (CL1, CLZ); bullpen contains no roll logic.

**Trading calendars (guide 8).** `TradingCalendar` holds a time zone, regular session times, a weekmask (Monday to
Friday by default) and `regular_open_day_offset` (-1 for sessions that open the evening before, like Globex). Nothing
is stored for an ordinary day: only `TradingCalendarException` rows for holidays and special sessions. Questions:
`is_trading_day`, `get_session` (time-zone-aware open and close), `get_next_trading_day`, `get_previous_trading_day`.
Exchanges have a default calendar; a futures root may override it.

**Quote conventions (guide 9).** A canonical price is in major units of the instrument's currency, so
`notional = price * multiplier * quantity`; a canonical futures quantity is in contracts. Where a symbology differs
(corn in cents, London equities in pence, a clearer reporting barrels), `QuoteConventionForFuturesRoot` or
`QuoteConventionForInstrument` stores `price_factor` and `quantity_factor` (`canonical = vendor * factor`).
`quote_convention.get_quote_convention(session, symbology, instrument, as_of)` walks the symbology chain, most specific
first, preferring an instrument's own row to its root's within one symbology, and returns a `QuoteConvention` that
converts both ways. Convert at the boundary; nothing inside bullpen holds a vendor price.

**Corporate actions (guide 10).** A `CorporateAction` is an event on one instrument with `ex_date`, `record_date`,
`pay_date`, `is_held_surrendered` (split, merger) and one or more `CorporateActionOutcome`s per unit held: cash
(currency, amount) or securities (instrument, quantity, `cost_basis_fraction`, which may arrive later). The
`CorporateActionType` is a label; the IBOR applies every type through the outcomes alone. The IBOR's question is
`CorporateAction.find_going_ex(session, instrument, from_ex_date, to_ex_date, known_at_datetime)`.

## Modules

| Module | Holds |
|---|---|
| `base`, `schema` | declarative base on schema `sec_master`; `create_all` / `drop_all` |
| `exchange` | `Exchange` |
| `instrument`, `instrument_type` | `Instrument` base, `InstrumentType` |
| `instrument_equity`, `instrument_futures_contract`, `instrument_synthetic` | subclasses; `InstrumentSyntheticLeg`, `LegWeight` |
| `futures_root`, `settlement_type` | `FuturesRoot`, `SettlementType` |
| `symbology` | `Symbology` |
| `symbol_for_exchange`, `symbol_for_futures_root`, `symbol_for_instrument` | symbol rows |
| `symbology_for_exchange`, `symbology_for_futures_root`, `symbology_for_instrument` | symbol operations per target |
| `trading_calendar` | `TradingCalendar`, `TradingCalendarException`, `TradingSession` |
| `quote_convention_for_futures_root`, `quote_convention_for_instrument`, `quote_convention` | convention rows; `QuoteConvention` and lookup |
| `corporate_action`, `corporate_action_type` | `CorporateAction`, `CorporateActionOutcome`, `CorporateActionType` |

## User guide

| Guide | Teaches |
|---|---|
| 01 exchange symbologies | canonical MIC, vendor symbols, inheritance, symbols over time |
| 02 instruments | the two time axes, changes and corrections, tickers |
| 03 futures symbology | root symbols, contract symbols from templates |
| 04 futures model | joined subclass tables, root corrections reaching contracts |
| 05 mandate symbologies | research names, rolls as leg changes |
| 06 synthetic instruments | VIX30, spreads, straddles, bounded leg corrections |
| 07 reading as of | `AsOf`, identity tokens, traversal |
| 08 trading calendars | weekly pattern, holidays, early closes, time zones, Globex |
| 09 quote conventions | price and quantity factors, inheritance, dated changes |
| 10 corporate actions | dividends, splits, spin-offs, mergers, amendments, cancellation |

## Not built yet

- Option instruments (strike, right, expiry; strike quote conventions).
- Instrument end of life (delisting, expiry) as an explicit state.
- Settlement calendars per currency and settlement-date rules; futures expiry rules.
- Text price notations such as Treasury 32nds.
- Corporate-action elections, rights issues, cash in lieu, withholding tax, price-adjustment factors.
- Integrity checks on stable-key references, concurrent-writer locking, and the command queue behind `command_id`.
- Units of synthetic leg weights (contracts, notional, or fraction of NAV) are not yet defined.
