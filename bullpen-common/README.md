# bullpen-common

Part of [BullPen](../README.md). The types that cross package and process boundaries: wire DTOs exchanged between the
server and the client, shared enums, id and money types, and the command envelope. It depends only on pydantic and
never on a sibling package, so both the server and the client can depend on it.

What belongs here: a shape both sides of a boundary must agree on exactly. What does not: ORM tables, SQLAlchemy, or
anything about how one side handles the data (see `AGENTS.md` rule 8). Bi-temporal persistence helpers live in
`bullpen-temporal` instead, so the client never depends on SQLAlchemy.

## Status

Scaffold. `instrument_dto.InstrumentDto` is an early draft that nothing uses yet; it will be replaced when the server
grows its first endpoints.

## Running

```sh
poetry install
poetry run pytest -q
```
