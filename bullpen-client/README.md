# bullpen-client

Part of [BullPen](../README.md). A typed Python client for the BullPen REST API, built on httpx. It speaks only the
wire types in `bullpen-common`, so it can be installed on a trader's or analyst's machine without SQLAlchemy, Postgres
drivers or any server code.

## Planned design

- Typed query methods returning `bullpen-common` DTOs, with `AsOf`-style parameters (effective date and known-at time)
  so a client can reproduce a past report.
- Command submission (`POST /commands`), optionally waiting for the result.

## Status

Scaffold: no methods yet; they follow the server's first endpoints.
