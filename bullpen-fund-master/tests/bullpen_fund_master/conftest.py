from datetime import date, datetime, timezone
from typing import Iterator

import pytest
import sqlalchemy
from sqlalchemy import orm
from testcontainers.community import postgres

import bullpen_fund_master.broker as broker
import bullpen_fund_master.fund as fund
import bullpen_fund_master.mandate as mandate
import bullpen_fund_master.schema as schema

BEGIN_DATE: date = date(2020, 1, 1)
FIRST_KNOWN_DATETIME: datetime = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def engine() -> Iterator[sqlalchemy.Engine]:
    with postgres.PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        result: sqlalchemy.Engine = sqlalchemy.create_engine(container.get_connection_url())
        yield result
        result.dispose()


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def fund_master_schema(engine: sqlalchemy.Engine) -> Iterator[None]:
    schema.create_all(engine)
    yield
    schema.drop_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def session(engine: sqlalchemy.Engine, fund_master_schema: None) -> Iterator[orm.Session]:
    with orm.Session(engine) as result:
        yield result
        result.rollback()


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def fund_by_code(session: orm.Session) -> dict[str, fund.Fund]:
    """An onshore and an offshore fund."""
    result: dict[str, fund.Fund] = {}
    for fund_code, fund_name in [("MAIN", "Main Fund LP"), ("OFFSHORE", "Offshore Fund Ltd")]:
        result[fund_code] = fund.Fund.create(session, BEGIN_DATE, FIRST_KNOWN_DATETIME, fund_code, fund_name, "USD")
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def mandate_by_code(session: orm.Session) -> dict[str, mandate.Mandate]:
    """Two strategies, each run against its own research weights."""
    result: dict[str, mandate.Mandate] = {}
    for mandate_code, mandate_name in [("ALPHA", "Alpha Trend"), ("BETA", "Beta Carry")]:
        result[mandate_code] = mandate.Mandate.create(
            session, BEGIN_DATE, FIRST_KNOWN_DATETIME, mandate_code, mandate_name
        )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def broker_by_code(session: orm.Session) -> dict[str, broker.Broker]:
    """Two prime brokers."""
    result: dict[str, broker.Broker] = {}
    for broker_code, broker_name in [("GS", "Goldman Sachs"), ("MS", "Morgan Stanley")]:
        result[broker_code] = broker.Broker.create(session, BEGIN_DATE, FIRST_KNOWN_DATETIME, broker_code, broker_name)
    return result
