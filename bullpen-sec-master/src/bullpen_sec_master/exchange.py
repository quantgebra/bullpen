from sqlalchemy import orm

import bullpen_sec_master.base as base


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
