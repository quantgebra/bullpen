from typing import Iterator

import pytest
import sqlalchemy
from sqlalchemy import orm
from testcontainers.community import postgres

import bullpen_ibor.schema as schema


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
