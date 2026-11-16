import sqlalchemy

import bullpen_ibor.base as base
import bullpen_ibor.event  # noqa: F401  (registers mappers)
import bullpen_ibor.trade  # noqa: F401


# ----------------------------------------------------------------------------------------------------------------------
def create_all(engine: sqlalchemy.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS ibor"))
    base.Base.metadata.create_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
def drop_all(engine: sqlalchemy.Engine) -> None:
    base.Base.metadata.drop_all(engine)
