# bullpen-temporal

Part of [BullPen](../README.md). Shared bi-temporal persistence for the master-data packages: every fact has when it
was true in the world (`effective_range`) and when we believed it (`knowledge_range`). It depends only on SQLAlchemy,
and is kept out of `bullpen-common` so the client never pulls in SQLAlchemy.

## Modules

- `as_of`: `AsOf(effective_date, known_at_datetime)`, the (D, K) every bi-temporal read takes. As-of reads use it as
  SQLAlchemy's `identity_token`, so one row read at two `AsOf`s is two objects in one session, and each row remembers
  what it was read at: `get_as_of_from_row(row)`.
- `bitemporal_row`: the `BitemporalRow` mixin. It supplies `row_id`, `effective_range`, `knowledge_range`,
  `command_id`, `end()`, the as-of finders `find_one_as_of` and `find_all_as_of`, and the supersede machinery
  (`_create_replacement`, `_supersede_from`, `_close_believed_rows`).

## Using the mixin

A class declares its stable key, an EXCLUDE constraint over key and both ranges, and two methods:

```python
class Fund(bitemporal_row.BitemporalRow, base.Base):
    __tablename__ = "funds"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("fund_id", "="), ("effective_range", "&&"), ("knowledge_range", "&&"), name="funds_no_overlap", using="gist"
        ),
    )

    fund_id: orm.Mapped[int] = orm.mapped_column(server_default=FUND_ID_SEQ.next_value(), index=True)
    name: orm.Mapped[str]

    def with_new_name(self, session, effective_from_date, known_at_datetime, name) -> "Fund":
        replacement_row: Fund = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        return [Fund.fund_id == self.fund_id]

    def _copy_with_ranges(self, effective_range, knowledge_range) -> "Fund":
        return Fund(fund_id=self.fund_id, effective_range=effective_range, knowledge_range=knowledge_range, ...)
```

A change must be made on the latest believed row; otherwise it raises, because later facts would be lost silently. A
row holding a single fact (a symbol, a pair of quote factors) sets `MAY_SUPERSEDE_EARLIER_ROWS = True`, so a
correction may be dated before the row's own start. `_close_believed_rows` also takes an optional
`effective_to_date`, superseding a bounded window and keeping what lies after it (synthetic legs use this).

## Status

Used by every bi-temporal class in `bullpen-sec-master` and `bullpen-fund-master`. Knowledge-only records (calendar
exceptions, corporate actions, IBOR trades) do not use it. It has no tests of its own; it is exercised through those
packages' user guides.
