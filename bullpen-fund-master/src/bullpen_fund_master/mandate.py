from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_fund_master.base as base
import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row

MANDATE_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("mandate_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Mandate(bitemporal_row.BitemporalRow, base.Base):
    """
    An investment strategy run against research weights (ALPHA). Not owned by a fund: each fund that runs it does so
    through a Sleeve. Bi-temporal, stable key mandate_id; code is unique at any time.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "mandates"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("mandate_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="mandates_no_overlap",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("code", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="mandates_code_unique",
            using="gist",
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        code: str,
        name: str,
    ) -> "Mandate":
        """A new mandate with a new mandate_id, from effective_from_date."""
        created_row: Mandate = Mandate(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            code=code,
            name=name,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, mandate_id: int, as_of: as_of_module.AsOf) -> Optional["Mandate"]:
        result: Optional[Mandate] = Mandate.find_one_as_of(session, [Mandate.mandate_id == mandate_id], as_of)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_code_as_of(session: orm.Session, code: str, as_of: as_of_module.AsOf) -> Optional["Mandate"]:
        result: Optional[Mandate] = Mandate.find_one_as_of(session, [Mandate.code == code], as_of)
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    mandate_id: orm.Mapped[int] = orm.mapped_column(server_default=MANDATE_ID_SEQ.next_value(), index=True)
    code: orm.Mapped[str]
    name: orm.Mapped[str]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_name(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, name: str
    ) -> "Mandate":
        """From effective_from_date on the mandate has a new name; self becomes a superseded belief."""
        replacement_row: Mandate = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [Mandate.mandate_id == self.mandate_id]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "Mandate":
        result: Mandate = Mandate(
            mandate_id=self.mandate_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            code=self.code,
            name=self.name,
            command_id=self.command_id,
        )
        return result
