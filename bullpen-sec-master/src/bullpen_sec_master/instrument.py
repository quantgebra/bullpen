from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument_type as instrument_types

INSTRUMENT_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("instrument_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Instrument(bitemporal_row.BitemporalRow, base.Base):
    """
    One bi-temporal table for every instrument type. instrument_id is the stable key (same on every row of one
    instrument, allocated from a sequence); row_id is just this row. Type-specific facts live in joined subclass tables
    (e.g. instrument_futures_contracts) keyed by row_id, so they share the row's two ranges.

    effective_range: when the facts were true in the world. knowledge_range: when we believed them. Rows are never
    edited except to close knowledge_range; each concrete subclass has its own create(), and
    changes are with_new_xxx() methods that supersede the row and return its replacement.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "instruments"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("instrument_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="instruments_no_overlap",
            using="gist",
        ),
        # an instrument's type never changes: no two rows of one instrument_id may differ in instrument_type
        postgresql.ExcludeConstraint(
            ("instrument_id", "="), ("instrument_type", "<>"), name="instruments_type_immutable", using="gist"
        ),
    )
    __mapper_args__ = {"polymorphic_on": "instrument_type", "with_polymorphic": "*"}

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, instrument_id: int, as_of: as_of_module.AsOf) -> Optional["Instrument"]:
        """The instrument (as its concrete subclass) as believed at as_of's K, true on its D; it remembers as_of."""
        result: Optional[Instrument] = Instrument.find_one_as_of(
            session, [Instrument.instrument_id == instrument_id], as_of
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    instrument_id: orm.Mapped[int] = orm.mapped_column(server_default=INSTRUMENT_ID_SEQ.next_value(), index=True)
    instrument_type: orm.Mapped[instrument_types.InstrumentType] = orm.mapped_column(
        sqlalchemy.Enum(instrument_types.InstrumentType, name="instrument_type")
    )
    name: orm.Mapped[str]
    currency_code: orm.Mapped[str] = orm.mapped_column(sqlalchemy.String(3))

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_name(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, name: str
    ) -> "Instrument":
        """From effective_from_date onward this instrument has a new name; self becomes a superseded belief."""
        replacement_row: Instrument = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_currency_code(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, currency_code: str
    ) -> "Instrument":
        """From effective_from_date onward this instrument has a new currency; self becomes a superseded belief."""
        replacement_row: Instrument = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.currency_code = currency_code
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        """Each concrete subclass implements _copy_with_ranges(), copying its own columns (and details table)."""
        result: list[sqlalchemy.ColumnElement] = [Instrument.instrument_id == self.instrument_id]
        return result
