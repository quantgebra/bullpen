from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterator

import pytest
import sqlalchemy
from sqlalchemy import orm
from testcontainers.community import postgres

import bullpen_sec_master.exchange as exchange
import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.schema as schema
import bullpen_sec_master.settlement_type as settlement_types
import bullpen_sec_master.symbology as symbology
import bullpen_sec_master.symbology_for_exchange as symbology_for_exchange
import bullpen_sec_master.symbology_for_futures_root as symbology_for_futures_root

FIRST_KNOWN_DATETIME: datetime = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
SECOND_KNOWN_DATETIME: datetime = datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc)
THIRD_KNOWN_DATETIME: datetime = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
BEGIN_DATE: date = date(2020, 1, 1)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def engine() -> Iterator[sqlalchemy.Engine]:
    with postgres.PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        result: sqlalchemy.Engine = sqlalchemy.create_engine(container.get_connection_url())
        yield result
        result.dispose()


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def sec_master_schema(engine: sqlalchemy.Engine) -> Iterator[None]:
    schema.create_all(engine)
    yield
    schema.drop_all(engine)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def session(engine: sqlalchemy.Engine, sec_master_schema: None) -> Iterator[orm.Session]:
    with orm.Session(engine) as result:
        yield result
        result.rollback()


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def exchange_by_mic(session: orm.Session) -> dict[str, exchange.Exchange]:
    """Four exchanges keyed by MIC. XTST is a made-up exchange that no vendor renames."""
    result: dict[str, exchange.Exchange] = {
        mic: exchange.Exchange(mic=mic, name=mic) for mic in ["XCME", "XCBT", "XNYM", "XTST"]
    }
    session.add_all(result.values())
    session.flush()
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def futures_root_by_code(
    session: orm.Session, exchange_by_mic: dict[str, exchange.Exchange]
) -> dict[str, futures_root.FuturesRoot]:
    """Futures roots CL (NYMEX crude) and ES (CME S&P 500 e-mini), keyed by canonical root name."""
    result: dict[str, futures_root.FuturesRoot] = {}
    for root_code, mic, multiplier_text, tick_size_text, settlement_type in [
        ("CL", "XNYM", "1000", "0.01", settlement_types.SettlementType.PHYSICAL),
        ("ES", "XCME", "50", "0.25", settlement_types.SettlementType.CASH),
    ]:
        result[root_code] = futures_root.FuturesRoot.create(
            session,
            BEGIN_DATE,
            FIRST_KNOWN_DATETIME,
            exchange=exchange_by_mic[mic],
            name=root_code,
            currency_code="USD",
            multiplier=Decimal(multiplier_text),
            tick_size=Decimal(tick_size_text),
            settlement_type=settlement_type,
        )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def vendor_symbology_by_code(
    session: orm.Session,
    exchange_by_mic: dict[str, exchange.Exchange],
    futures_root_by_code: dict[str, futures_root.FuturesRoot],
) -> dict[str, symbology.Symbology]:
    """
    Each vendor symbology names exchanges, roots and contracts at once. FUT_COMMON supplies the default contract
    template. IBKR renames three CME-group venues; BBG has its own template; CSI calls CL "21" and has its own template.
    """
    common_symbology: symbology.Symbology = symbology.Symbology.create(
        session, "FUT_COMMON", contract_template="{root}{month_code}{y}"
    )
    result: dict[str, symbology.Symbology] = {
        "FUT_COMMON": common_symbology,
        "IBKR": symbology.Symbology.create(session, "IBKR", parent=common_symbology),
        "BBG": symbology.Symbology.create(
            session, "BBG", parent=common_symbology, contract_template="{root}{month_code}{y} Comdty"
        ),
        "CSI": symbology.Symbology.create(session, "CSI", parent=common_symbology, contract_template="{root}_{yyyymm}"),
    }
    symbology_for_exchange.create_symbol(
        session, result["IBKR"], BEGIN_DATE, FIRST_KNOWN_DATETIME, exchange_by_mic["XCME"], "GLOBEX"
    )
    symbology_for_exchange.create_symbol(
        session, result["IBKR"], BEGIN_DATE, FIRST_KNOWN_DATETIME, exchange_by_mic["XCBT"], "ECBOT"
    )
    symbology_for_exchange.create_symbol(
        session, result["IBKR"], BEGIN_DATE, FIRST_KNOWN_DATETIME, exchange_by_mic["XNYM"], "NYMEX"
    )
    symbology_for_futures_root.create_symbol(
        session, result["CSI"], BEGIN_DATE, FIRST_KNOWN_DATETIME, futures_root_by_code["CL"], "21"
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def clz6_future_contract(
    session: orm.Session, futures_root_by_code: dict[str, futures_root.FuturesRoot]
) -> instrument_futures_contract.InstrumentFuturesContract:
    """The CL December 2026 contract."""
    result: instrument_futures_contract.InstrumentFuturesContract = (
        instrument_futures_contract.InstrumentFuturesContract.create(
            session,
            date(2025, 6, 1),
            FIRST_KNOWN_DATETIME,
            name="Crude Oil Dec 2026",
            currency_code="USD",
            futures_root=futures_root_by_code["CL"],
            contract_month=date(2026, 12, 1),
            last_trade_date=date(2026, 11, 20),
            first_notice_date=None,
            expiry_date=date(2026, 11, 20),
        )
    )
    return result
