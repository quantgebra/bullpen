from datetime import date, datetime
from typing import Optional, Self

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module


# ======================================================================================================================
class BitemporalRow:
    """
    Mixin for a bi-temporal entity stored as rows of one table: effective_range is when the facts were true in the
    world, knowledge_range when we believed them. Rows are never edited except to close knowledge_range; a change
    closes the believed rows from a date on and appends a replacement. Every row of one entity shares its stable key.

    A subclass declares its stable-key column(s), an EXCLUDE constraint over key + both ranges, and implements
    _get_same_entity_conditions() and _copy_with_ranges().
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    # A row holding one fact (a symbol, a pair of quote factors) may be superseded from a date before its own start,
    # e.g. to correct when a ticker changed: the replacement restates everything the earlier rows said. A row holding
    # several facts may not, because its copy would silently overwrite the earlier rows' other facts.
    MAY_SUPERSEDE_EARLIER_ROWS: bool = False

    # ------------------------------------------------------------------------------------------------------------------
    @classmethod
    def find_one_as_of(
        cls, session: orm.Session, conditions: list[sqlalchemy.ColumnElement], as_of: as_of_module.AsOf
    ) -> Optional[Self]:
        """The row matching conditions, true on as_of's date as believed at its time; it remembers as_of."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(cls).where(
            *conditions,
            cls.effective_range.contains(as_of.effective_date),
            cls.knowledge_range.contains(as_of.known_at_datetime),
        )
        result: Optional[Self] = session.scalars(select_statement, execution_options={"identity_token": as_of}).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @classmethod
    def find_all_as_of(
        cls,
        session: orm.Session,
        conditions: list[sqlalchemy.ColumnElement],
        as_of: as_of_module.AsOf,
        order_by_column: sqlalchemy.ColumnElement,
    ) -> list[Self]:
        """Every row matching conditions at as_of, ordered by order_by_column; each remembers as_of."""
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(cls)
            .where(
                *conditions,
                cls.effective_range.contains(as_of.effective_date),
                cls.knowledge_range.contains(as_of.known_at_datetime),
            )
            .order_by(order_by_column)
        )
        result: list[Self] = list(session.scalars(select_statement, execution_options={"identity_token": as_of}).all())
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @classmethod
    def _close_believed_rows(
        cls,
        session: orm.Session,
        conditions: list[sqlalchemy.ColumnElement],
        effective_from_date: date,
        effective_to_date: Optional[date],
        known_at_datetime: datetime,
    ) -> None:
        """
        Stop believing every row matching conditions inside [effective_from_date, effective_to_date) (open-ended if
        effective_to_date is None) from known_at_datetime on, and re-insert the parts of those rows outside the window
        as new beliefs, so facts outside it are kept.
        """
        window_date_range: postgresql.Range[date] = postgresql.Range(
            effective_from_date, effective_to_date, bounds="[)"
        )
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(cls)
            .where(
                *conditions,
                sqlalchemy.func.upper_inf(cls.knowledge_range),
                cls.effective_range.overlaps(window_date_range),
            )
            .order_by(sqlalchemy.func.lower(cls.effective_range))
        )
        for closed_row in session.scalars(select_statement).all():
            # stop believing the whole row first, so the kept parts below cannot overlap it
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()

            # keep the part before the window
            if old_effective_range.lower < effective_from_date:
                before_row: Self = closed_row._copy_with_ranges(
                    postgresql.Range(old_effective_range.lower, effective_from_date, bounds="[)"),
                    postgresql.Range(known_at_datetime, None, bounds="[)"),
                )
                session.add(before_row)

            # keep the part after the window (only a bounded window has an after)
            if effective_to_date is not None:
                if old_effective_range.upper is None or old_effective_range.upper > effective_to_date:
                    after_row: Self = closed_row._copy_with_ranges(
                        postgresql.Range(effective_to_date, old_effective_range.upper, bounds="[)"),
                        postgresql.Range(known_at_datetime, None, bounds="[)"),
                    )
                    session.add(after_row)
        session.flush()

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def end(self, session: orm.Session, effective_to_date: date, known_at_datetime: datetime) -> None:
        """From effective_to_date on, the entity no longer exists (a fund liquidated, a ticker retired)."""
        self._supersede_from(session, effective_to_date, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def _create_replacement(self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime) -> Self:
        """
        Supersede this entity from effective_from_date and return an unsaved copy open from that date: the caller sets
        the changed field, then adds and flushes it.
        """
        self._supersede_from(session, effective_from_date, known_at_datetime)
        result: Self = self._copy_with_ranges(
            postgresql.Range(effective_from_date, None, bounds="[)"),
            postgresql.Range(known_at_datetime, None, bounds="[)"),
        )
        result.command_id = None
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede_from(self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime) -> None:
        """Only the latest believed row can be the basis of a change, otherwise later facts would be lost silently."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"{type(self).__name__} row {self.row_id} is no longer believed")
        if self.effective_range.upper is not None:
            raise ValueError(f"{type(self).__name__} row {self.row_id} has later believed rows; change the latest one")
        if not type(self).MAY_SUPERSEDE_EARLIER_ROWS:
            if self.effective_range.lower > effective_from_date:
                raise ValueError(f"{type(self).__name__} row {self.row_id} starts after {effective_from_date}")
        type(self)._close_believed_rows(
            session, self._get_same_entity_conditions(), effective_from_date, None, known_at_datetime
        )

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        """Conditions selecting every row of this entity: its stable key equals self's."""
        raise NotImplementedError

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> Self:
        """A new, unsaved row of this entity with the same facts and the given ranges."""
        raise NotImplementedError
