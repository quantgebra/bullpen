from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_fund_master.base as base
import bullpen_fund_master.broker as broker_module
import bullpen_fund_master.fund as fund_module
import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row

ACCOUNT_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("account_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Account(bitemporal_row.BitemporalRow, base.Base):
    """
    An account a fund holds at a broker. code is the broker's account number, unique within the broker at any time.
    Every sleeve of the fund may trade through it; the broker sees only the account's totals. Bi-temporal, stable key
    account_id; fund_id and broker_id are stable keys resolved as-of, not FKs.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "accounts"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("account_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="accounts_no_overlap",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("broker_id", "="),
            ("code", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="accounts_code_unique_at_broker",
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
        broker: broker_module.Broker,
        code: str,
        name: str,
    ) -> "Account":
        """A new account of fund at broker, with a new account_id, opened on effective_from_date."""
        created_row: Account = Account(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            fund_id=fund.fund_id,
            broker_id=broker.broker_id,
            code=code,
            name=name,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, account_id: int, as_of: as_of_module.AsOf) -> Optional["Account"]:
        result: Optional[Account] = Account.find_one_as_of(session, [Account.account_id == account_id], as_of)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_code_as_of(
        session: orm.Session, broker: broker_module.Broker, code: str, as_of: as_of_module.AsOf
    ) -> Optional["Account"]:
        """The account the broker calls code, e.g. to resolve the account on a broker statement."""
        result: Optional[Account] = Account.find_one_as_of(
            session, [Account.broker_id == broker.broker_id, Account.code == code], as_of
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_for_fund_as_of(session: orm.Session, fund: fund_module.Fund, as_of: as_of_module.AsOf) -> list["Account"]:
        result: list[Account] = Account.find_all_as_of(
            session, [Account.fund_id == fund.fund_id], as_of, Account.account_id
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    account_id: orm.Mapped[int] = orm.mapped_column(server_default=ACCOUNT_ID_SEQ.next_value(), index=True)
    fund_id: orm.Mapped[int] = orm.mapped_column(index=True)
    broker_id: orm.Mapped[int] = orm.mapped_column(index=True)
    code: orm.Mapped[str]
    name: orm.Mapped[str]

    # ------------------------------------------------------------------------------------------------------------------
    def get_fund(self, session: orm.Session) -> Optional[fund_module.Fund]:
        """The owning fund as believed at the same AsOf this account was read at."""
        result: Optional[fund_module.Fund] = fund_module.Fund.find_as_of(
            session, self.fund_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_broker(self, session: orm.Session) -> Optional[broker_module.Broker]:
        """The broker as believed at the same AsOf this account was read at."""
        result: Optional[broker_module.Broker] = broker_module.Broker.find_as_of(
            session, self.broker_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_name(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, name: str
    ) -> "Account":
        """From effective_from_date on the account has a new name; self becomes a superseded belief."""
        replacement_row: Account = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.name = name
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [Account.account_id == self.account_id]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "Account":
        result: Account = Account(
            account_id=self.account_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            fund_id=self.fund_id,
            broker_id=self.broker_id,
            code=self.code,
            name=self.name,
            command_id=self.command_id,
        )
        return result
