from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.base as base
import bullpen_sec_master.corporate_action_type as corporate_action_types
import bullpen_sec_master.instrument as instrument_module

CORPORATE_ACTION_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence(
    "corporate_action_id_seq", metadata=base.Base.metadata
)


# ======================================================================================================================
class CorporateActionOutcome(base.Base):
    """
    What a holder receives per unit of the held instrument: either cash (currency_code, cash_per_unit_held) or
    securities (instrument_id, quantity_per_unit_held). cost_basis_fraction is the share of the held position's cost
    basis that moves to received securities; None until the issuer publishes it. Belongs to one CorporateAction row and
    is only manipulated through it.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "corporate_action_outcomes"
    __table_args__ = (
        # an outcome is cash or securities, never both
        sqlalchemy.CheckConstraint(
            "(instrument_id IS NOT NULL AND quantity_per_unit_held IS NOT NULL"
            " AND currency_code IS NULL AND cash_per_unit_held IS NULL)"
            " OR (instrument_id IS NULL AND quantity_per_unit_held IS NULL AND cost_basis_fraction IS NULL"
            " AND currency_code IS NOT NULL AND cash_per_unit_held IS NOT NULL)",
            name="corporate_action_outcomes_cash_or_securities",
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def build_cash(currency_code: str, cash_per_unit_held: Decimal) -> "CorporateActionOutcome":
        """An unsaved cash outcome, to pass to CorporateAction.create() or with_new_outcomes()."""
        result: CorporateActionOutcome = CorporateActionOutcome(
            currency_code=currency_code, cash_per_unit_held=cash_per_unit_held
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def build_securities(
        instrument: instrument_module.Instrument,
        quantity_per_unit_held: Decimal,
        cost_basis_fraction: Optional[Decimal] = None,
    ) -> "CorporateActionOutcome":
        """An unsaved securities outcome, to pass to CorporateAction.create() or with_new_outcomes()."""
        result: CorporateActionOutcome = CorporateActionOutcome(
            instrument_id=instrument.instrument_id,
            quantity_per_unit_held=quantity_per_unit_held,
            cost_basis_fraction=cost_basis_fraction,
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    corporate_action_row_id: orm.Mapped[int] = orm.mapped_column(
        sqlalchemy.ForeignKey("sec_master.corporate_actions.row_id"), index=True
    )
    instrument_id: orm.Mapped[Optional[int]]  # stable key, not an FK
    quantity_per_unit_held: orm.Mapped[Optional[Decimal]] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    cost_basis_fraction: orm.Mapped[Optional[Decimal]] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    currency_code: orm.Mapped[Optional[str]] = orm.mapped_column(sqlalchemy.String(3))
    cash_per_unit_held: orm.Mapped[Optional[Decimal]] = orm.mapped_column(sqlalchemy.Numeric(28, 10))

    # ------------------------------------------------------------------------------------------------------------------
    def is_cash(self) -> bool:
        result: bool = self.currency_code is not None
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def build_copy(self) -> "CorporateActionOutcome":
        """An unsaved copy, for an amended action row that keeps these outcomes."""
        result: CorporateActionOutcome = CorporateActionOutcome(
            instrument_id=self.instrument_id,
            quantity_per_unit_held=self.quantity_per_unit_held,
            cost_basis_fraction=self.cost_basis_fraction,
            currency_code=self.currency_code,
            cash_per_unit_held=self.cash_per_unit_held,
        )
        return result


# ======================================================================================================================
class CorporateAction(base.Base):
    """
    An event that changes what holders of an instrument own: a dividend, split, spin-off or merger. It is about its own
    dates (ex, record, pay), so there is no effective_range; knowledge_range records when we believed these terms,
    because actions are announced, amended (dates, ratios, cost-basis allocation) and cancelled. corporate_action_id is
    the stable key. The type is a label for people; the outcomes, plus is_held_surrendered, are what the IBOR applies.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "corporate_actions"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("corporate_action_id", "="),
            ("knowledge_range", "&&"),
            name="corporate_actions_no_overlap",
            using="gist",
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        known_at_datetime: datetime,
        instrument: instrument_module.Instrument,
        corporate_action_type: corporate_action_types.CorporateActionType,
        ex_date: date,
        record_date: Optional[date],
        pay_date: Optional[date],
        is_held_surrendered: bool,
        outcomes: list[CorporateActionOutcome],
    ) -> "CorporateAction":
        """
        A new action on instrument, believed from known_at_datetime. is_held_surrendered: holders give up the held
        instrument (split, merger) rather than keep it (dividend, spin-off).
        """
        created_row: CorporateAction = CorporateAction(
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            instrument_id=instrument.instrument_id,
            corporate_action_type=corporate_action_type,
            ex_date=ex_date,
            record_date=record_date,
            pay_date=pay_date,
            is_held_surrendered=is_held_surrendered,
        )
        created_row._add_with_outcomes(session, outcomes)
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(
        session: orm.Session, corporate_action_id: int, known_at_datetime: datetime
    ) -> Optional["CorporateAction"]:
        """The action's terms as believed at known_at_datetime, or None if it was not (or no longer) believed then."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(CorporateAction).where(
            CorporateAction.corporate_action_id == corporate_action_id,
            CorporateAction.knowledge_range.contains(known_at_datetime),
        )
        result: Optional[CorporateAction] = session.scalars(select_statement).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_going_ex(
        session: orm.Session,
        instrument: instrument_module.Instrument,
        from_ex_date: date,
        to_ex_date: date,
        known_at_datetime: datetime,
    ) -> list["CorporateAction"]:
        """Actions on instrument with ex_date in [from_ex_date, to_ex_date), as believed at known_at_datetime."""
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(CorporateAction)
            .where(
                CorporateAction.instrument_id == instrument.instrument_id,
                CorporateAction.ex_date >= from_ex_date,
                CorporateAction.ex_date < to_ex_date,
                CorporateAction.knowledge_range.contains(known_at_datetime),
            )
            .order_by(CorporateAction.ex_date, CorporateAction.corporate_action_id)
        )
        result: list[CorporateAction] = list(session.scalars(select_statement).all())
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    corporate_action_id: orm.Mapped[int] = orm.mapped_column(
        server_default=CORPORATE_ACTION_ID_SEQ.next_value(), index=True
    )
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)  # stable key, not an FK
    corporate_action_type: orm.Mapped[corporate_action_types.CorporateActionType] = orm.mapped_column(
        sqlalchemy.Enum(corporate_action_types.CorporateActionType, name="corporate_action_type")
    )
    ex_date: orm.Mapped[date] = orm.mapped_column(index=True)
    record_date: orm.Mapped[Optional[date]]
    pay_date: orm.Mapped[Optional[date]]
    is_held_surrendered: orm.Mapped[bool]
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def get_outcomes(self, session: orm.Session) -> list[CorporateActionOutcome]:
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(CorporateActionOutcome)
            .where(CorporateActionOutcome.corporate_action_row_id == self.row_id)
            .order_by(CorporateActionOutcome.row_id)
        )
        result: list[CorporateActionOutcome] = list(session.scalars(select_statement).all())
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_dates(
        self,
        session: orm.Session,
        known_at_datetime: datetime,
        ex_date: date,
        record_date: Optional[date],
        pay_date: Optional[date],
    ) -> "CorporateAction":
        """From known_at_datetime on, the action has new dates; self becomes a superseded belief."""
        kept_outcomes: list[CorporateActionOutcome] = self._copy_outcomes(session)
        self._supersede(session, known_at_datetime)
        replacement_row: CorporateAction = self._copy_with_knowledge_range(
            postgresql.Range(known_at_datetime, None, bounds="[)")
        )
        replacement_row.ex_date = ex_date
        replacement_row.record_date = record_date
        replacement_row.pay_date = pay_date
        replacement_row._add_with_outcomes(session, kept_outcomes)
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_outcomes(
        self, session: orm.Session, known_at_datetime: datetime, outcomes: list[CorporateActionOutcome]
    ) -> "CorporateAction":
        """From known_at_datetime on, holders receive outcomes instead; self becomes a superseded belief."""
        self._supersede(session, known_at_datetime)
        replacement_row: CorporateAction = self._copy_with_knowledge_range(
            postgresql.Range(known_at_datetime, None, bounds="[)")
        )
        replacement_row._add_with_outcomes(session, outcomes)
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def cancel(self, session: orm.Session, known_at_datetime: datetime) -> None:
        """From known_at_datetime on, the action is not believed at all; earlier runs still see it."""
        self._supersede(session, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def _add_with_outcomes(self, session: orm.Session, outcomes: list[CorporateActionOutcome]) -> None:
        """Insert this row, then its outcomes, which need its row_id."""
        if len(outcomes) == 0:
            raise ValueError("a corporate action needs at least one outcome")

        session.add(self)
        session.flush()

        for outcome in outcomes:
            outcome.corporate_action_row_id = self.row_id
            session.add(outcome)
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_outcomes(self, session: orm.Session) -> list[CorporateActionOutcome]:
        result: list[CorporateActionOutcome] = []
        for outcome in self.get_outcomes(session):
            copied_outcome: CorporateActionOutcome = outcome.build_copy()
            result.append(copied_outcome)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede(self, session: orm.Session, known_at_datetime: datetime) -> None:
        """Only the latest believed row can be the basis of a change, otherwise later facts would be lost silently."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"corporate action {self.corporate_action_id} row {self.row_id} is no longer believed")
        self.knowledge_range = postgresql.Range(self.knowledge_range.lower, known_at_datetime, bounds="[)")
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_knowledge_range(self, knowledge_range: postgresql.Range[datetime]) -> "CorporateAction":
        result: CorporateAction = CorporateAction(
            corporate_action_id=self.corporate_action_id,
            knowledge_range=knowledge_range,
            instrument_id=self.instrument_id,
            corporate_action_type=self.corporate_action_type,
            ex_date=self.ex_date,
            record_date=self.record_date,
            pay_date=self.pay_date,
            is_held_surrendered=self.is_held_surrendered,
            command_id=self.command_id,
        )
        return result
