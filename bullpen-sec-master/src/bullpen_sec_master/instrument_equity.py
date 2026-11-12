from datetime import date, datetime

from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_type as instrument_types


# ======================================================================================================================
class InstrumentEquity(instrument.Instrument):
    """No extra columns yet: single-table subclass, just a Python type for the discriminator."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __mapper_args__ = {"polymorphic_identity": instrument_types.InstrumentType.EQUITY}

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        name: str,
        currency_code: str,
    ) -> "InstrumentEquity":
        """A new equity with a new instrument_id."""
        created_row: InstrumentEquity = InstrumentEquity(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            name=name,
            currency_code=currency_code,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "InstrumentEquity":
        result: InstrumentEquity = InstrumentEquity(
            instrument_id=self.instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            name=self.name,
            currency_code=self.currency_code,
            command_id=self.command_id,
        )
        return result
