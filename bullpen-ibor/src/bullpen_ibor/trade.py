from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_ibor.base as base
import bullpen_ibor.event as event_module
import bullpen_ibor.event_type as event_types
import bullpen_ibor.source_type as source_types

TRADE_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("trade_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class Trade(base.Base):
    """
    An allocated trade as received from the OMS or a broker (the per-account block at its average price; fills stay in
    the OMS), and the source of the events it causes. Trades are amended and cancelled after the fact, so they are
    versioned like corporate actions: trade_id is the stable key, each row is one version believed during
    knowledge_range, and events point at the row (the version) they were computed from. Superseding a version reverses
    its events; the new version books its own.

    quantity is signed (positive buys). cash_amount is the signed settlement amount in cash_instrument_id as the broker
    states it (negative for a buy), so the IBOR never needs a multiplier or a quote convention to book cash. For a
    futures trade it is zero and no cash event is booked. executed_at_datetime is when the block was executed (its last
    fill); trade_date is the session it belongs to, which for an evening Globex trade is the next calendar day.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "trades"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("trade_id", "="),
            ("knowledge_range", "&&"),
            name="trades_no_overlap",
            using="gist",
        ),
        sqlalchemy.CheckConstraint("quantity <> 0", name="trades_quantity_nonzero"),
        sqlalchemy.CheckConstraint("settlement_date >= trade_date", name="trades_settle_on_or_after_trade"),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def book_trade(
        session: orm.Session,
        known_at_datetime: datetime,
        fund_id: int,
        mandate_id: int,
        account_id: int,
        portfolio_instrument_id: int,
        traded_instrument_id: int,
        quantity: Decimal,
        price: Decimal,
        cash_instrument_id: int,
        cash_amount: Decimal,
        executed_at_datetime: datetime,
        trade_date: date,
        settlement_date: date,
        external_ref: Optional[str] = None,
    ) -> "Trade":
        """
        Record a new trade in the book (fund_id, mandate_id, account_id) and append its events: the traded quantity
        and, unless it is zero, the cash. The caller has checked the book against the fund master.
        """
        trade_row: Trade = Trade(
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            fund_id=fund_id,
            mandate_id=mandate_id,
            account_id=account_id,
            portfolio_instrument_id=portfolio_instrument_id,
            traded_instrument_id=traded_instrument_id,
            quantity=quantity,
            price=price,
            cash_instrument_id=cash_instrument_id,
            cash_amount=cash_amount,
            executed_at_datetime=executed_at_datetime,
            trade_date=trade_date,
            settlement_date=settlement_date,
            external_ref=external_ref,
        )
        trade_row._add_with_events(session)
        return trade_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, trade_id: int, known_at_datetime: datetime) -> Optional["Trade"]:
        """The trade's terms as believed at known_at_datetime, or None if it was not (or no longer) believed then."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(Trade).where(
            Trade.trade_id == trade_id,
            Trade.knowledge_range.contains(known_at_datetime),
        )
        result: Optional[Trade] = session.scalars(select_statement).first()
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.BigInteger, primary_key=True)
    trade_id: orm.Mapped[int] = orm.mapped_column(server_default=TRADE_ID_SEQ.next_value(), index=True)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    fund_id: orm.Mapped[int]  # fund-master stable keys, by id only: the command handler checks that they fit
    mandate_id: orm.Mapped[int]
    account_id: orm.Mapped[int]
    portfolio_instrument_id: orm.Mapped[int]
    traded_instrument_id: orm.Mapped[int]
    quantity: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    price: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    cash_instrument_id: orm.Mapped[int]
    cash_amount: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    executed_at_datetime: orm.Mapped[datetime] = orm.mapped_column(sqlalchemy.DateTime(timezone=True))
    trade_date: orm.Mapped[date]
    settlement_date: orm.Mapped[date]
    external_ref: orm.Mapped[Optional[str]]
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_terms(
        self,
        session: orm.Session,
        known_at_datetime: datetime,
        quantity: Decimal,
        price: Decimal,
        cash_amount: Decimal,
        executed_at_datetime: datetime,
        trade_date: date,
        settlement_date: date,
    ) -> "Trade":
        """
        The broker amended the trade: from known_at_datetime on, it has these economic terms. self's events are
        reversed and the returned version books its own; the book and instruments stay (moving a trade to another book
        is a cancel and a new trade).
        """
        self._supersede(session, known_at_datetime)

        replacement_row: Trade = Trade(
            trade_id=self.trade_id,
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            fund_id=self.fund_id,
            mandate_id=self.mandate_id,
            account_id=self.account_id,
            portfolio_instrument_id=self.portfolio_instrument_id,
            traded_instrument_id=self.traded_instrument_id,
            quantity=quantity,
            price=price,
            cash_instrument_id=self.cash_instrument_id,
            cash_amount=cash_amount,
            executed_at_datetime=executed_at_datetime,
            trade_date=trade_date,
            settlement_date=settlement_date,
            external_ref=self.external_ref,
        )
        replacement_row._add_with_events(session)
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def cancel(self, session: orm.Session, known_at_datetime: datetime) -> None:
        """The trade was busted or booked in error: from known_at_datetime on it is not believed; its events net out."""
        self._supersede(session, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def get_events(self, session: orm.Session) -> list[event_module.Event]:
        """The events this version of the trade caused, including their reversals, in the order they were appended."""
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(event_module.Event)
            .where(
                event_module.Event.source_type == source_types.SourceType.TRADE,
                event_module.Event.source_row_id == self.row_id,
            )
            .order_by(event_module.Event.event_id)
        )
        result: list[event_module.Event] = list(session.scalars(select_statement).all())
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _add_with_events(self, session: orm.Session) -> None:
        """Insert this version, then its events, which need its row_id as their source."""
        session.add(self)
        session.flush()

        # the traded quantity is attributed to the portfolio security
        quantity_event: event_module.Event = self._build_event(
            event_types.EventType.TRADE_QUANTITY, self.portfolio_instrument_id, self.traded_instrument_id, self.quantity
        )
        session.add(quantity_event)

        # cash is its own portfolio security: it is the mandate's cash, not part of the position that was traded
        if self.cash_amount != 0:
            cash_event: event_module.Event = self._build_event(
                event_types.EventType.TRADE_CASH, self.cash_instrument_id, self.cash_instrument_id, self.cash_amount
            )
            session.add(cash_event)
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def _build_event(
        self,
        event_type: event_types.EventType,
        portfolio_instrument_id: int,
        traded_instrument_id: int,
        delta_quantity: Decimal,
    ) -> event_module.Event:
        """An unsaved event caused by this trade version, in its book, on its dates and known-at time."""
        result: event_module.Event = event_module.Event(
            fund_id=self.fund_id,
            mandate_id=self.mandate_id,
            account_id=self.account_id,
            portfolio_instrument_id=portfolio_instrument_id,
            traded_instrument_id=traded_instrument_id,
            event_type=event_type,
            delta_quantity=delta_quantity,
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
            effective_datetime=self.executed_at_datetime,
            known_at_datetime=self.knowledge_range.lower,
            source_type=source_types.SourceType.TRADE,
            source_row_id=self.row_id,
            command_id=self.command_id,
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede(self, session: orm.Session, known_at_datetime: datetime) -> None:
        """Stop believing this version from known_at_datetime on, and reverse every event it caused."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"trade {self.trade_id} version {self.row_id} is no longer believed")
        if known_at_datetime <= self.knowledge_range.lower:
            raise ValueError(f"trade {self.trade_id} cannot change at or before it was learned")

        # reverse first: get_events must see only this version's original events
        for original_event in self.get_events(session):
            reversal_event: event_module.Event = original_event.build_reversal(known_at_datetime, None)
            session.add(reversal_event)

        self.knowledge_range = postgresql.Range(self.knowledge_range.lower, known_at_datetime, bounds="[)")
        session.flush()
