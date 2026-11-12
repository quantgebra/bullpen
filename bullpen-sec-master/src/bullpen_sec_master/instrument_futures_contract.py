from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.futures_root as futures_root_module
import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_type as instrument_types


# ======================================================================================================================
class InstrumentFuturesContract(instrument.Instrument):
    """A contract such as CLZ6. Joined to instruments by row_id, so these facts share the instrument row's two ranges."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "instrument_futures_contracts"
    __mapper_args__ = {"polymorphic_identity": instrument_types.InstrumentType.FUTURE}

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        name: str,
        currency_code: str,
        futures_root: futures_root_module.FuturesRoot,
        contract_month: date,
        last_trade_date: date,
        expiry_date: date,
        first_notice_date: Optional[date] = None,
    ) -> "InstrumentFuturesContract":
        """A new futures contract with a new instrument_id."""
        created_row: InstrumentFuturesContract = InstrumentFuturesContract(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            name=name,
            currency_code=currency_code,
            futures_root_id=futures_root.futures_root_id,
            contract_month=contract_month,
            last_trade_date=last_trade_date,
            first_notice_date=first_notice_date,
            expiry_date=expiry_date,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(
        sqlalchemy.ForeignKey("sec_master.instruments.row_id"), primary_key=True
    )
    futures_root_id: orm.Mapped[int] = orm.mapped_column(index=True)  # stable key; resolved as-of, not an FK
    contract_month: orm.Mapped[date]
    last_trade_date: orm.Mapped[date]
    first_notice_date: orm.Mapped[Optional[date]]
    expiry_date: orm.Mapped[date]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_expiry_date(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, expiry_date: date
    ) -> "InstrumentFuturesContract":
        """From effective_from_date onward the contract expires on expiry_date; self becomes a superseded belief."""
        self._supersede_from(session, effective_from_date, known_at_datetime)
        replacement_row: InstrumentFuturesContract = self._copy_with_ranges(
            postgresql.Range(effective_from_date, None, bounds="[)"),
            postgresql.Range(known_at_datetime, None, bounds="[)"),
        )
        replacement_row.command_id = None
        replacement_row.expiry_date = expiry_date
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def get_root(self, session: orm.Session) -> Optional[futures_root_module.FuturesRoot]:
        """The root as believed at the same AsOf this contract was read at."""
        result: Optional[futures_root_module.FuturesRoot] = futures_root_module.FuturesRoot.find_as_of(
            session, self.futures_root_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_contract_multiplier(self, session: orm.Session) -> Decimal:
        futures_root_row: Optional[futures_root_module.FuturesRoot] = self.get_root(session)
        result: Decimal = futures_root_row.multiplier
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "InstrumentFuturesContract":
        result: InstrumentFuturesContract = InstrumentFuturesContract(
            instrument_id=self.instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            name=self.name,
            currency_code=self.currency_code,
            command_id=self.command_id,
            futures_root_id=self.futures_root_id,
            contract_month=self.contract_month,
            last_trade_date=self.last_trade_date,
            first_notice_date=self.first_notice_date,
            expiry_date=self.expiry_date,
        )
        return result
