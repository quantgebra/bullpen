# bullpen-temporal

Shared bi-temporal persistence for bullpen's master-data packages. It depends only on SQLAlchemy, and is kept out of
`bullpen-common` so the client never pulls in SQLAlchemy.

- `as_of`: `AsOf(effective_date, known_at_datetime)`, the (D, K) every bi-temporal read takes, used as SQLAlchemy's
  `identity_token`; `get_as_of_from_row(row)` returns the `AsOf` a row was read at.
- `bitemporal_row`: the `BitemporalRow` mixin. It supplies `row_id`, `effective_range`, `knowledge_range`, `command_id`,
  `end()`, as-of finders (`find_one_as_of`, `find_all_as_of`) and the supersede/replace machinery
  (`_create_replacement`, `_supersede_from`, `_close_believed_rows`). A subclass declares its stable key, an EXCLUDE
  constraint over key and both ranges, and implements `_get_same_entity_conditions()` and `_copy_with_ranges()`.

A row holding a single fact (a symbol, a pair of quote factors) sets `MAY_SUPERSEDE_EARLIER_ROWS = True`, so a
correction may be dated before the row's own start. `_close_believed_rows` also takes an optional `effective_to_date`,
superseding a bounded window and keeping what lies after it (synthetic legs use this).

Used by every bi-temporal class in `bullpen-sec-master` and `bullpen-fund-master`. Knowledge-only events (calendar
exceptions, corporate actions, trades) do not use it.
