from typing import Iterator

import pytest
import sqlalchemy
from sqlalchemy import orm
from testcontainers.community import postgres

import bullpen_ibor.schema as schema

# fund-master ids (fund, mandates, accounts) and securities-master ids (instruments): the IBOR stores ids only
MAIN_FUND_ID: int = 1
ALPHA_MANDATE_ID: int = 11
BETA_MANDATE_ID: int = 12
GS_ACCOUNT_ID: int = 21
MS_ACCOUNT_ID: int = 22

USD_CASH_INSTRUMENT_ID: int = 1
APPLE_INSTRUMENT_ID: int = 101
MICROSOFT_INSTRUMENT_ID: int = 102
CLZ6_INSTRUMENT_ID: int = 201
ALPHA_CL_SYNTHETIC_INSTRUMENT_ID: int = 501


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def engine() -> Iterator[sqlalchemy.Engine]:
    with postgres.PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        result: sqlalchemy.Engine = sqlalchemy.create_engine(container.get_connection_url())
        yield result
        result.dispose()


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def ibor_schema(engine: sqlalchemy.Engine) -> Iterator[None]:
    schema.create_all(engine)
    yield
    schema.drop_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def session(engine: sqlalchemy.Engine, ibor_schema: None) -> Iterator[orm.Session]:
    with orm.Session(engine) as result:
        yield result
        result.rollback()
