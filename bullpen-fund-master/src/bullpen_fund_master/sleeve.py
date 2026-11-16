from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_fund_master.account as account_module
import bullpen_fund_master.base as base
import bullpen_fund_master.fund as fund_module
import bullpen_fund_master.mandate as mandate_module
import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row


# ======================================================================================================================
class Sleeve(bitemporal_row.BitemporalRow, base.Base):
    """
    A fund running a mandate: the association of a Mandate with a Fund, keyed (fund_id, mandate_id). One mandate may
    run in several funds, each its own sleeve. Bi-temporal, because a fund starts and stops running a mandate and we
    learn of it late; its effective_range is when the fund ran the mandate.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "sleeves"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("fund_id", "="),
            ("mandate_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="sleeves_no_overlap",
            using="gist",
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        fund: fund_module.Fund,
        mandate: mandate_module.Mandate,
    ) -> "Sleeve":
        """From effective_from_date on, the fund runs the mandate."""
        created_row: Sleeve = Sleeve(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            fund_id=fund.fund_id,
            mandate_id=mandate.mandate_id,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, fund_id: int, mandate_id: int, as_of: as_of_module.AsOf) -> Optional["Sleeve"]:
        """The sleeve if the fund ran the mandate on as_of's date, as believed at its time."""
        result: Optional[Sleeve] = Sleeve.find_one_as_of(
            session, [Sleeve.fund_id == fund_id, Sleeve.mandate_id == mandate_id], as_of
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_for_fund_as_of(session: orm.Session, fund: fund_module.Fund, as_of: as_of_module.AsOf) -> list["Sleeve"]:
        """The mandates a fund runs: its fund structure on as_of's date."""
        result: list[Sleeve] = Sleeve.find_all_as_of(
            session, [Sleeve.fund_id == fund.fund_id], as_of, Sleeve.mandate_id
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_for_mandate_as_of(
        session: orm.Session, mandate: mandate_module.Mandate, as_of: as_of_module.AsOf
    ) -> list["Sleeve"]:
        """The funds that run a mandate on as_of's date."""
        result: list[Sleeve] = Sleeve.find_all_as_of(
            session, [Sleeve.mandate_id == mandate.mandate_id], as_of, Sleeve.fund_id
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    fund_id: orm.Mapped[int] = orm.mapped_column(index=True)  # stable key, resolved as-of, not an FK
    mandate_id: orm.Mapped[int] = orm.mapped_column(index=True)  # stable key, resolved as-of, not an FK

    # ------------------------------------------------------------------------------------------------------------------
    def get_fund(self, session: orm.Session) -> Optional[fund_module.Fund]:
        """The fund as believed at the same AsOf this sleeve was read at."""
        result: Optional[fund_module.Fund] = fund_module.Fund.find_as_of(
            session, self.fund_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_mandate(self, session: orm.Session) -> Optional[mandate_module.Mandate]:
        """The mandate as believed at the same AsOf this sleeve was read at."""
        result: Optional[mandate_module.Mandate] = mandate_module.Mandate.find_as_of(
            session, self.mandate_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def can_trade_through(self, account: account_module.Account) -> bool:
        """A sleeve trades only through accounts of its own fund; read the account at the same AsOf as the sleeve."""
        result: bool = account.fund_id == self.fund_id
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [Sleeve.fund_id == self.fund_id, Sleeve.mandate_id == self.mandate_id]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "Sleeve":
        result: Sleeve = Sleeve(
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            fund_id=self.fund_id,
            mandate_id=self.mandate_id,
            command_id=self.command_id,
        )
        return result
