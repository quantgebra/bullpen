import sqlalchemy

import bullpen_sec_master.base as base
import bullpen_sec_master.corporate_action  # noqa: F401
import bullpen_sec_master.exchange  # noqa: F401
import bullpen_sec_master.futures_root  # noqa: F401
import bullpen_sec_master.instrument  # noqa: F401
import bullpen_sec_master.instrument_equity  # noqa: F401  (registers mappers)
import bullpen_sec_master.instrument_futures_contract  # noqa: F401
import bullpen_sec_master.instrument_synthetic  # noqa: F401
import bullpen_sec_master.quote_convention_for_futures_root  # noqa: F401
import bullpen_sec_master.quote_convention_for_instrument  # noqa: F401
import bullpen_sec_master.symbol_for_exchange  # noqa: F401
import bullpen_sec_master.symbol_for_futures_root  # noqa: F401
import bullpen_sec_master.symbol_for_instrument  # noqa: F401
import bullpen_sec_master.symbology  # noqa: F401
import bullpen_sec_master.trading_calendar  # noqa: F401


# ----------------------------------------------------------------------------------------------------------------------
def create_all(engine: sqlalchemy.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS sec_master"))
    base.Base.metadata.create_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
def drop_all(engine: sqlalchemy.Engine) -> None:
    base.Base.metadata.drop_all(engine)
