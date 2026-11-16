# bullpen-fund-master

Part of [BullPen](../README.md). Who owns what and who decides what: funds, mandates, sleeves, brokers and accounts,
bi-temporal, in Postgres schema `fund_master`. Other packages (the IBOR, later the OMS and NAV) refer to these by stable
id only. The design is in [`../docs/architecture.md`](../docs/architecture.md) §4a; the executable specification is the
user guide under `tests/bullpen_fund_master/`.

## Running

```sh
poetry install
poetry run pytest -q          # starts postgres:17-alpine with testcontainers; needs Docker
```

## Model

| Class | Stable key | Holds |
|---|---|---|
| `Fund` | `fund_id` | a legal entity that owns positions: code (unique at any time), name, base currency |
| `Mandate` | `mandate_id` | a strategy run against research weights; not owned by a fund |
| `Sleeve` | `(fund_id, mandate_id)` | a fund running a mandate; one mandate may run in several funds |
| `Broker` | `broker_id` | a prime broker or custodian |
| `Account` | `account_id` | a fund's account at a broker; `code` is the broker's account number, unique within it |

Every class is bi-temporal through `bullpen_temporal.bitemporal_row.BitemporalRow`: `effective_range` is when it
existed or had these facts, `knowledge_range` when we believed it. Changes are `with_new_name(...)`; `end(...)` closes
an entity from a date (a fund liquidated, a sleeve stopped, an account closed). Reads take an `AsOf`; traversals
(`sleeve.get_fund`, `sleeve.get_mandate`, `account.get_fund`, `account.get_broker`) reuse the row's `AsOf`.

## Checking a book

The IBOR's book `(fund_id, mandate_id, account_id)` fits when its sleeve exists at the trade's `AsOf` and the account
belongs to the sleeve's fund:

```python
sleeve = Sleeve.find_as_of(session, fund_id, mandate_id, as_of)
fits = sleeve is not None and sleeve.can_trade_through(Account.find_as_of(session, account_id, as_of))
```

Command handlers do this before booking; there are no foreign keys across package schemas.

## Not built yet

Share classes and series. Order-flow routes belong to the OMS.
