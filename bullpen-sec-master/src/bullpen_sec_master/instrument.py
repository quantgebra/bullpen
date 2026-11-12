from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument_type as instrument_types

INSTRUMENT_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("instrument_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Instrument(base.Base):
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
        select_statement: sqlalchemy.Select = sqlalchemy.select(Instrument).where(
            Instrument.instrument_id == instrument_id,
            Instrument.effective_range.contains(as_of.effective_date),
            Instrument.knowledge_range.contains(as_of.known_at_datetime),
        )
        result: Optional[Instrument] = session.scalars(
            select_statement, execution_options={"identity_token": as_of}
        ).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _close_believed_rows(
        session: orm.Session, instrument_id: int, effective_from_date: date, known_at_datetime: datetime
    ) -> list["Instrument"]:
        """
        Supersede everything currently believed from effective_from_date onward: close each such row's
        knowledge_range and re-insert the part of it that lies before effective_from_date. Returns the closed rows.
        """
        open_ended_date_range: postgresql.Range[date] = postgresql.Range(effective_from_date, None, bounds="[)")
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(Instrument)
            .where(
                Instrument.instrument_id == instrument_id,
                sqlalchemy.func.upper_inf(Instrument.knowledge_range),
                Instrument.effective_range.overlaps(open_ended_date_range),
            )
            .order_by(sqlalchemy.func.lower(Instrument.effective_range))
        )
        closed_rows: list[Instrument] = list(session.scalars(select_statement).all())
        for closed_row in closed_rows:
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()
            if old_effective_range.lower < effective_from_date:
                session.add(
                    closed_row._copy_with_ranges(
                        postgresql.Range(old_effective_range.lower, effective_from_date, bounds="[)"),
                        postgresql.Range(known_at_datetime, None, bounds="[)"),
                    )
                )
        return closed_rows

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    instrument_id: orm.Mapped[int] = orm.mapped_column(server_default=INSTRUMENT_ID_SEQ.next_value(), index=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    instrument_type: orm.Mapped[instrument_types.InstrumentType] = orm.mapped_column(
        sqlalchemy.Enum(instrument_types.InstrumentType, name="instrument_type")
    )
    name: orm.Mapped[str]
    currency_code: orm.Mapped[str] = orm.mapped_column(sqlalchemy.String(3))
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_name(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, name: str
    ) -> "Instrument":
        """From effective_from_date onward this instrument has a new name; self becomes a superseded belief."""
        self._supersede_from(session, effective_from_date, known_at_datetime)
        replacement_row: Instrument = self._copy_with_ranges(
            postgresql.Range(effective_from_date, None, bounds="[)"),
            postgresql.Range(known_at_datetime, None, bounds="[)"),
        )
        replacement_row.command_id = None
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_currency_code(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, currency_code: str
    ) -> "Instrument":
        """From effective_from_date onward this instrument has a new currency; self becomes a superseded belief."""
        self._supersede_from(session, effective_from_date, known_at_datetime)
        replacement_row: Instrument = self._copy_with_ranges(
            postgresql.Range(effective_from_date, None, bounds="[)"),
            postgresql.Range(known_at_datetime, None, bounds="[)"),
        )
        replacement_row.command_id = None
        replacement_row.currency_code = currency_code
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede_from(self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime) -> None:
        """Only the latest believed row can be the basis of a change, otherwise later facts would be lost silently."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"instrument {self.instrument_id} row {self.row_id} is no longer believed")
        if self.effective_range.upper is not None:
            raise ValueError(f"instrument {self.instrument_id} has later believed rows; change the latest one")
        if self.effective_range.lower > effective_from_date:
            raise ValueError(f"instrument {self.instrument_id} has earlier believed rows; change the earliest one")
        Instrument._close_believed_rows(session, self.instrument_id, effective_from_date, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "Instrument":
        """Each concrete subclass copies its own columns (including its details table) explicitly."""
        raise NotImplementedError
