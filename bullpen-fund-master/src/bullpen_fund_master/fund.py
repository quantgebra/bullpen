from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_fund_master.base as base
import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row

FUND_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("fund_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Fund(bitemporal_row.BitemporalRow, base.Base):
    """A legal fund, the owner of positions and accounts. Bi-temporal, stable key fund_id; code is unique at any time."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "funds"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("fund_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="funds_no_overlap",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("code", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="funds_code_unique",
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
        base_currency_code: str,
    ) -> "Fund":
        """A new fund with a new fund_id, launched on effective_from_date."""
        created_row: Fund = Fund(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            code=code,
            name=name,
            base_currency_code=base_currency_code,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, fund_id: int, as_of: as_of_module.AsOf) -> Optional["Fund"]:
        result: Optional[Fund] = Fund.find_one_as_of(session, [Fund.fund_id == fund_id], as_of)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_code_as_of(session: orm.Session, code: str, as_of: as_of_module.AsOf) -> Optional["Fund"]:
        result: Optional[Fund] = Fund.find_one_as_of(session, [Fund.code == code], as_of)
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    fund_id: orm.Mapped[int] = orm.mapped_column(server_default=FUND_ID_SEQ.next_value(), index=True)
    code: orm.Mapped[str]
    name: orm.Mapped[str]
    base_currency_code: orm.Mapped[str] = orm.mapped_column(sqlalchemy.String(3))

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_name(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, name: str
    ) -> "Fund":
        """From effective_from_date on the fund has a new name; self becomes a superseded belief."""
        replacement_row: Fund = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [Fund.fund_id == self.fund_id]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "Fund":
        result: Fund = Fund(
            fund_id=self.fund_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            code=self.code,
            name=self.name,
            base_currency_code=self.base_currency_code,
            command_id=self.command_id,
        )
        return result
