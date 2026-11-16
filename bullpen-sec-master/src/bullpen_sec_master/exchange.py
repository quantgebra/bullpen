from typing import Optional

import sqlalchemy
from sqlalchemy import orm

import bullpen_sec_master.base as base
import bullpen_sec_master.trading_calendar as trading_calendar_module


# ======================================================================================================================
class Exchange(base.Base):
    """Venue. Plain (not bi-temporal) so it can be a real FK target. Vendor names live in symbols_for_exchanges."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "exchanges"

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    mic: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]
    trading_calendar_id: orm.Mapped[Optional[int]] = orm.mapped_column(
        sqlalchemy.ForeignKey("sec_master.trading_calendars.id")
    )

    # ------------------------------------------------------------------------------------------------------------------
    def get_trading_calendar(self, session: orm.Session) -> Optional[trading_calendar_module.TradingCalendar]:
        """The exchange's default trading calendar; a futures root may override it."""
        result: Optional[trading_calendar_module.TradingCalendar] = None
        if self.trading_calendar_id is not None:
            result = session.get(trading_calendar_module.TradingCalendar, self.trading_calendar_id)
        return result
