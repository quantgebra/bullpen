# Bullpen — Architecture

Status: **in progress**. The securities master, the fund master and IBOR slice 1 are implemented; the rest is
planned. Bullpen is a clean-room successor to trinity-fms
(securities master, Gen 3 IBOR, NAV, OMS, reconciliation). Code style follows `AGENTS.md`.

---

## 1. Goals

1. A **Gen 3 IBOR**: positions, cash and P&L are *projections over an immutable, append-only, bi-temporal event
   ledger*. There is no mutable position table and no stored snapshot that is authoritative.
2. Reference data (the securities master first) is bi-temporal as well: "what did we believe this instrument looked
   like on effective date E, as known at time K?"
3. Hard component boundaries: separate packages with separate dependencies, schemas and migrations.
4. No Redis. Writes are commands persisted in a Postgres command queue.

## 2. Repository topology

One git repository, independent Poetry packages (no workspace tooling; sibling dependencies are path dependencies).

| Package | Import name | Responsibility | Depends on |
|---|---|---|---|
| `bullpen-common` | `bullpen_common` | Wire DTOs, enums, ID/money types, command envelope. No sibling deps. | — |
| `bullpen-temporal` | `bullpen_temporal` | Shared bi-temporal persistence: `AsOf`, the `BitemporalRow` mixin. Kept out of common so the client does not depend on SQLAlchemy. | — |
| `bullpen-sec-master` | `bullpen_sec_master` | Instruments, listings, identifiers, calendars, currencies, corporate-action definitions. | common, temporal |
| `bullpen-fund-master` | `bullpen_fund_master` | Funds, mandates, sleeves, accounts, brokers (later: share classes, series). | common, temporal |
| `bullpen-ibor` | `bullpen_ibor` | Event ledger, position extractor, lots, cash ladder, P&L. | common (sec-master and fund-master by ID only) |
| `bullpen-server` | `bullpen_server` | REST API (queries + command submission), command-queue worker. | common, sec-master, ibor |
| `bullpen-client` | `bullpen_client` | Typed httpx client. | common |

Planned later: `bullpen-market-data` (prices, exchange rates), `bullpen-oms` (including order-flow routes), `bullpen-nav`,
`bullpen-recon`, `bullpen-ui`.

Rules:
- Packages never import each other's ORM tables. Cross-package references are by ID and by `bullpen-common` DTOs.
- No foreign keys across package schemas. Command handlers, which may read several packages, check that the ids they
  write fit together (e.g. a book's sleeve exists and its account belongs to its fund).
- Each package owns a Postgres **schema** (`sec_master`, `fund_master`, `ibor`, `cmd`, ...) and its own Alembic branch.
- The server is the only place that composes packages.

## 3. Write path: REST + Postgres command queue

Trinity routed every write through a `JobRequest` on Redis queues. Bullpen keeps the idea (every state change is an
explicit, audited command) and replaces the transport with a table.

```
client ──POST /commands──► bullpen-server ──INSERT──► cmd.command (status=QUEUED)
                                                          │  SELECT … FOR UPDATE SKIP LOCKED
                                                          ▼
                                                   command worker(s)
                                                          │  one DB transaction per command:
                                                          │  run handler → append ledger events →
                                                          │  insert follow-up commands → mark DONE
                                                          ▼
                                             ibor.event / sec_master.* / cmd.command_log
```

- `cmd.command`: `id`, `type` (fully qualified handler name), `payload` (JSONB, validated by the handler's pydantic
  model), `idempotency_key` (unique), `priority`, `run_after`, `status` (QUEUED / RUNNING / DONE / FAILED / DEAD),
  `attempts`, `submitted_by`, `submitted_at`, `parent_command_id`.
- Workers claim with `FOR UPDATE SKIP LOCKED`; wake-ups use `LISTEN/NOTIFY` with a polling fallback.
- Handler effects and the status transition commit **atomically**, so follow-up commands and events are never
  visible before the work that caused them (replaces trinity's "publish after commit").
- Every ledger/reference row carries `command_id` for provenance (successor to trinity's `job_run_id`).
- Synchronous commands: `POST /commands?wait=true` runs the same handler in-process for the same audit trail.
- Reads are plain `GET`s that never go through the queue (CQRS).

## 4. Gen 3 IBOR

Implemented in the `bullpen_ibor` package (Postgres schema `ibor`); the guide tests in `bullpen-ibor/tests` are the
executable spec. Slice 1 (booking trades, positions, roll-ups) is done.

- **Books.** A book is the `(fund_id, mandate_id, account_id)` triple of fund-master ids, stored as it is on every
  trade and event: there is no `books` table and no book id. `(fund_id, mandate_id)` is a sleeve. The IBOR has no
  foreign keys to the fund master; the command handler checks that the book fits before booking.
- **One append-only table, `ibor.events`,** never updated or deleted. Each event is a signed `delta_quantity` of
  `traded_instrument_id`, held in the book for `portfolio_instrument_id` (usually a synthetic), with `trade_date`,
  `settlement_date`, `known_at_datetime`, `event_type`, `command_id`, and `reverses_event_id` (unique: an event is
  reversed at most once). Instrument ids are securities-master stable keys, referenced by id only.
- **Knowledge is an instant, not a range.** A sec-master correction replaces a belief; a ledger correction is a
  compensating event that offsets the mistake. Positions stay `SUM(delta_quantity)` over events with
  `known_at_datetime <= K`, and every restatement is visible as events.
- **Sources.** Every event names the version of the business document that caused it: `source_type` (Postgres enum:
  `TRADE`, `CORPORATE_ACTION`, `CASH_TRANSFER`, `ADJUSTMENT`) and `source_row_id`, the row id of that version.
  `command_id` says which command wrote it; the source says why. Pointing at the version gives exact lineage and makes
  "events whose source has since been amended" a query. There is no FK (sources live in several tables and schemas);
  the command handler is the only writer.
- **Trades** (`ibor.trades`) are recorded as received from the OMS or broker and versioned like corporate actions:
  stable `trade_id`, one row per version with a `knowledge_range`. `Trade.book_trade(...)` records the trade and
  appends its events. `cash_amount` is the broker's signed settlement amount, so booking cash needs no multiplier or
  quote convention; a futures trade has zero cash and books only the quantity event.
- **Cash is an instrument quantity** (`InstrumentType.CASH`), held with itself as the portfolio security, so a buy is
  +shares and -dollars in one sum.
- **Views** are filters on the same sum: *traded* (`trade_date <= E`) and *settled* (`settlement_date <= E`), both at
  `known_at_datetime <= K`. Later: *working* (traded + live OMS orders) and *forecast* (working + unreleased requests).
  `position.get_book_positions`, `get_sleeve_positions` (over accounts), `get_account_positions` and
  `get_fund_positions` (by traded security only: what a broker reports).
- **The IBOR does not import the securities master.** Anything needing reference data (expanding a synthetic's legs,
  applying a corporate action, settlement dates) is a command handler that reads the sec-master and appends events.
- Snapshots are optional caches and may be discarded or rebuilt at any time. Day close is a knowledge-time marker, not
  a gate: late trades land normally and surface as restatements.

Next slices: corrections (amend and cancel trades via reversals), synthetic lineage against research weights, cash
(fees, transfers, cash ladder), corporate-action application, lots and realized P&L.

## 4a. Fund master

Implemented in the `bullpen_fund_master` package (Postgres schema `fund_master`); see
`bullpen-fund-master/tests/bullpen_fund_master/test_guide_01_fund_structure.py`.

- **Fund** (a legal entity that owns positions), **Mandate** (a strategy run against research weights; not owned by a
  fund), **Sleeve** (a fund running a mandate: the pair `(fund_id, mandate_id)`; one mandate may run in several funds),
  **Broker**, and **Account** (a fund's account at a broker; `code` is the broker's account number, unique within the
  broker). Any sleeve of a fund may trade through any of the fund's accounts.
- **Everything is bi-temporal**, because the fund structure and accounts change and are learned late. Each class uses
  the `bullpen_temporal.bitemporal_row.BitemporalRow` mixin (row id, both ranges, `command_id`, `end`, and the
  supersede/replace machinery); a class adds its stable key, an EXCLUDE constraint, `_get_same_entity_conditions` and
  `_copy_with_ranges`. Reads take an `AsOf`; traversals (`sleeve.get_fund`, `account.get_broker`) reuse the row's.
- **A book fits** when `Sleeve.find_as_of(session, fund_id, mandate_id, as_of)` exists and
  `sleeve.can_trade_through(account)`. Command handlers check this before the IBOR books anything.
- Later: share classes and series. Order-flow routes belong to the OMS.

## 5. Securities master: bi-temporal model

Implemented in the `bullpen_sec_master` package (Postgres schema `sec_master`); the guide tests in `bullpen-sec-master/tests` are the executable spec.

- **One table per concept, plural names:** `instruments`, `instrument_futures_contracts`, `instrument_synthetic_legs`,
  `futures_roots`, `exchanges`, `symbologies`, `symbols_for_exchanges`, `symbols_for_futures_roots`,
  `symbols_for_instruments`. No `<x>_version` tables, no identity tables.
- **Bi-temporal rows in the same table.** Each row has `effective_range` (`DATERANGE`) and `knowledge_range` (`TSTZRANGE`). A
  correction closes the old row's `knowledge_range` (the only permitted update) and appends replacements. An exclusion
  constraint forbids two rows of one stable key overlapping on both axes (needs `btree_gist`).
- **Stable key vs row id.** `row_id` is the row's PK; `instrument_id` / `futures_root_id` is the stable key, allocated
  from a sequence and repeated on every row of that entity. Consequence: **no foreign keys to a stable key** (it is not
  unique). References are by key and resolved with an as-of lookup (`find_as_of(session, key, as_of)`).
  Integrity that an identity table would give is enforced by constraints instead, e.g. type immutability is
  `EXCLUDE USING gist (instrument_id WITH =, instrument_type WITH <>)`.
- **Creating and changing.** Every bi-temporal class uses the `bullpen_temporal.bitemporal_row.BitemporalRow` mixin
  (row id, both ranges, `command_id`, `find_one_as_of` / `find_all_as_of`, `end`, and the supersede machinery); a class
  adds its stable key, an EXCLUDE constraint, `_get_same_entity_conditions` and `_copy_with_ranges`. `create` always
  allocates a new stable key. A change is an instance method named `with_new_<field with unit>` (`with_new_name`,
  `with_new_currency_code`, `with_new_expiry_date`, `with_new_tick_size`): it supersedes the row it is called on and
  returns the replacement, so the caller does not repeat unchanged values. It must be called on the latest believed row,
  otherwise it raises, because later facts would be lost silently. Rows holding a single fact (symbols, quote
  conventions) set `MAY_SUPERSEDE_EARLIER_ROWS`, so `with_new_symbol_text` may be dated before the row's start to
  correct a change date: the replacement restates everything the earlier rows said. Synthetic legs supersede a bounded
  window of the whole leg set. No `**kwargs` or change dicts.
- **Type-specific facts** live in joined subclass tables keyed by `row_id` (`instrument_futures_contracts` for `InstrumentFuturesContract`),
  so they share the row's two ranges. `with_new_*` clones every mapped column including subclass tables.
- **Futures roots** (CL, ES) are their own bi-temporal concept holding tick size, multiplier, settlement type, exchange.
  Tick value is derived. Contracts reference a root by `futures_root_id` and read it at the contract's own `AsOf`.
- **Symbols (symbology).** A `Symbology` (IBKR, BBG, CSI) is a plain row with an optional parent and an optional contract
  template. One symbology names exchanges, futures roots and instruments alike, so it has no target type. Each target
  type has an implicit *canonical* symbol (`exchanges.mic`, `futures_roots.name`; instruments have none), and a symbology
  stores only the symbols that differ from the canonical name or from its parent's. One symbol table per target type
  (`symbols_for_exchanges`, `symbols_for_futures_roots`, `symbols_for_instruments`: all bi-temporal; `symbols_for_exchanges` keeps a real FK to the plain `exchanges` table).
  The per-target behavior lives in three modules of stateless functions that take `session` and `symbology` on every
  call (no object holds a session): `symbology_for_exchange`, `symbology_for_futures_root`, `symbology_for_instrument`, each with
  `create_symbol`, `get_symbol` and `resolve_target` (which returns the Exchange / FuturesRoot / Instrument as of the given
  effective date and knowledge time; inbound resolution rejects an inherited symbol that a more
  specific symbology has renamed: shadowing). `symbology_for_instrument` also derives contract symbols from root symbol (same
  symbology) + template unless an explicit symbol exists. A symbol row changes through `with_new_symbol_text` and `end`.

  **Mandate symbologies (research names).** A research system sends weights keyed by its own names ("CL"). Each mandate
  gets its own symbology in which "CL" points at a synthetic instrument owned by that mandate (ALPHA's CL), whose single
  leg is the contract actually traded. The name → synthetic mapping is stable; a roll changes only the synthetic's leg,
  so the IBOR sees one continuous portfolio security across rolls. Both facts come from an external system and are
  stored as received (bi-temporal); there is no roll logic in bullpen. Limits: a synthetic has one name per symbology at
  a time, and an unmapped name resolves to `None` just like an unknown one. See `test_guide_05_mandate_symbologies.py`.

  **Synthetic instruments.** `InstrumentSynthetic` (`InstrumentType.SYNTHETIC`) is an instrument no broker can hold:
  VIX30, a calendar spread, a straddle, a mandate's research name. It has its own stable `instrument_id` and bi-temporal
  legs `(leg instrument, signed weight)` in `instrument_synthetic_legs`, stored as received and changed with `with_new_legs`
  (optionally over a bounded window, so correcting one day leaves the others alone). Legs are never synthetic, so
  decomposition is one step. In the IBOR, entries are keyed `(fund, mandate, account, portfolio_security,
  traded_security)`: the synthetic is the portfolio security and its legs are the traded securities, which gives
  lineage back to research weights without a separate target-line concept; account-level (broker) positions are sums
  over mandate and portfolio security. See `test_guide_06_synthetic_instruments.py`.

  **Futures roots are not instruments (decided).** A root names a market, not something that can be held, so it is never
  a portfolio security. Exposure to a futures market is always held through a synthetic that explicitly labels the roll
  policy (CL1, or CLZ stitching only December contracts), with the traded contract as its leg. Cross-mandate reporting
  by market goes through the traded contract's root.

  **Reading as of (D, K).** Reads take one `AsOf(effective_date, known_at_datetime)`. As-of queries load rows with the
  `AsOf` as SQLAlchemy's `identity_token`, so one row read at two `AsOf`s is two objects in one session, and each row
  knows its own `AsOf` (`bullpen_temporal.as_of.get_as_of_from_row(row)`). Traversals from a row (`contract.get_root(session)`,
  `synthetic.get_legs(session)`, `symbol_row.get_target(session)`) reuse that `AsOf`. Rows just written have no `AsOf`
  and must be re-read before traversing. Writes keep an explicit `effective_from_date` and `known_at_datetime`. Plain
  (non-temporal) rows such as exchanges and symbologies keep a single identity: they are fetched with `session.get`,
  not through relationships from bi-temporal rows, because a related row loaded through an as-of row inherits its
  token and would become a second copy. `Symbology.parent` is an ordinary relationship. See
  `test_guide_07_reading_as_of.py`.

  **Trading calendars.** `TradingCalendar` (XNYS, CME_GLOBEX_ENERGY) is a plain row with a time zone, regular session
  times and a weekmask (Monday first, Monday to Friday by default). Nothing is stored for an ordinary day; only
  exceptions are, as `TradingCalendarException` rows, one per date: a holiday or a special session with its own open
  and close. `regular_open_day_offset = -1` covers sessions that open the evening before (Globex). Exceptions have no
  effective range (the fact is about its own date) but do have a knowledge range, because closures are announced late,
  amended and cancelled; every question (`is_trading_day`, `get_session`, `get_next_trading_day`,
  `get_previous_trading_day`) takes `known_at_datetime`, so an order run stays reproducible. Exchanges have a default
  calendar and a futures root may override it (`root.get_trading_calendar(session)`). Calendars are loaded through
  commands; a library such as `exchange_calendars` may seed them but is never consulted at runtime. Settlement
  calendars and expiry rules are not modelled yet. See `test_guide_08_trading_calendars.py`.

  **Quote conventions.** A canonical price is in major units of the instrument's currency (dollars, not cents), so
  `notional = price * multiplier * quantity`; a canonical futures quantity is in contracts. Where a symbology writes
  numbers differently (CBOT corn in cents, London equities in pence, a clearer reporting crude in barrels) a
  bi-temporal `QuoteConventionForFuturesRoot` or `QuoteConventionForInstrument` row holds `price_factor` and
  `quantity_factor` (`canonical = vendor * factor`). `quote_convention.get_quote_convention(session, symbology,
  instrument, as_of)` walks the symbology chain, most specific first, and within one symbology prefers the
  instrument's own row to its root's; with nothing stored the symbology is canonical. The returned `QuoteConvention`
  converts both ways. Conversion happens at the boundary: nothing inside bullpen holds a vendor price. Text notations
  such as Treasury 32nds are not handled yet. See `test_guide_09_quote_conventions.py`.

  **Corporate actions.** A `CorporateAction` (stable key `corporate_action_id`) is an event on one instrument with an
  `ex_date`, optional `record_date` and `pay_date`, `is_held_surrendered` (split, merger) or not (dividend, spin-off),
  and one or more `CorporateActionOutcome` rows per unit held: cash (currency, amount) or securities (instrument,
  quantity, `cost_basis_fraction`, which may arrive later). The `CorporateActionType` is a label; the IBOR applies
  every type through the outcomes alone. Like calendar exceptions, actions have a knowledge range but no effective
  range: `with_new_dates`, `with_new_outcomes` and `cancel` are new beliefs, and `find_going_ex(session, instrument,
  from_ex_date, to_ex_date, known_at_datetime)` is the IBOR's question. Not modelled yet: elections (optional
  dividends, tender offers), rights issues, fractional-share cash-in-lieu, withholding tax, and price-adjustment
  factors for research history. See `test_guide_10_corporate_actions.py`.

**Naming convention:** see `AGENTS.md` rules 37–41 (concept-first variant names, `For` for things attached to a
target, names that carry their type, verb-first functions, tests as a user guide).

## 6. ORM choice: SQLAlchemy 2.0 (decided)

An early bake-off implemented the same bi-temporal instrument in SQLModel and in SQLAlchemy 2.0 (both spikes have since
been deleted). SQLAlchemy won because range types and exclusion constraints are central to this model, and that is
exactly where SQLModel's convenience falls away:

- SQLModel needs `sa_column=Column(DATERANGE)` plus `arbitrary_types_allowed` for every range, enum and numeric column,
  so it saves no boilerplate here; SQLAlchemy uses `Mapped[Range[date]] = mapped_column(DATERANGE)` uniformly.
- SQLModel requires its own `Session` (`.exec()`), so two session types would leak into shared code.
- SQLModel's one-class-for-table-and-API advantage is unwanted: wire types stay separate from tables.
- Trap found on the way (applies to both): a class attribute with the same name as an imported module (e.g.
  `instrument_type`) breaks declarative scanning under Python 3.14's lazy annotations; import such modules with a
  distinct alias.

Money and price fields are `NUMERIC`/`Decimal`, never float. Enums are stored as Postgres enums.

## 7. Open decisions

- Tenancy: single manager vs multi-tenant from the start.
- Whether `ibor.event` is partitioned (by `trade_date`) from day one.
- Settlement calendars (per currency) and settlement-date rules (T+1 equities, T+0 futures) — owned by
  `bullpen-sec-master`; trading calendars, quote conventions and corporate-action definitions are done.
- How the IBOR applies a corporate action (lot treatment, cash-in-lieu) — owned by `bullpen-ibor`.
