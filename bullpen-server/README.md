# bullpen-server

Part of [BullPen](../README.md). The only place that composes packages: a FastAPI REST service and the command-queue
worker that applies every write.

## Planned design

- **Reads** are plain `GET`s against the packages' query functions; they never go through the queue.
- **Writes** are commands: `POST /commands` inserts a row into `cmd.command` (Postgres, no Redis). Workers claim
  commands with `SELECT ... FOR UPDATE SKIP LOCKED` and run each handler in one transaction: the handler's effects,
  any follow-up commands and the status change commit together. `?wait=true` runs a command synchronously through the
  same path.
- **Handlers check what the database cannot.** There are no foreign keys across package schemas, so a handler that
  books a trade first checks the fund master (the sleeve exists, the account belongs to the fund) and the securities
  master, then calls the IBOR.
- Every row a command writes carries its `command_id`.

See [`../docs/architecture.md`](../docs/architecture.md) §3.

## Status

Scaffold: no endpoints or worker yet. `pyproject.toml` depends on `bullpen-sec-master` and `bullpen-ibor`;
`bullpen-fund-master` and `bullpen-temporal` will be added with the first handlers.
