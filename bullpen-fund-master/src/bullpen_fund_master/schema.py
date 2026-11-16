import sqlalchemy

import bullpen_fund_master.account  # noqa: F401  (registers mappers)
import bullpen_fund_master.base as base
import bullpen_fund_master.broker  # noqa: F401
import bullpen_fund_master.fund  # noqa: F401
import bullpen_fund_master.mandate  # noqa: F401
import bullpen_fund_master.sleeve  # noqa: F401


# ----------------------------------------------------------------------------------------------------------------------
def create_all(engine: sqlalchemy.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS fund_master"))
    base.Base.metadata.create_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
def drop_all(engine: sqlalchemy.Engine) -> None:
    base.Base.metadata.drop_all(engine)
