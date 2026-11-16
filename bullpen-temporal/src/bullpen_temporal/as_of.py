from datetime import date, datetime
from typing import NamedTuple

import sqlalchemy


# ======================================================================================================================
class AsOf(NamedTuple):

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    effective_date: date
    known_at_datetime: datetime


# ----------------------------------------------------------------------------------------------------------------------
def get_as_of_from_row(row: object) -> AsOf:
    """
    The (D, K) a bi-temporal row was read at. As-of queries load rows with the AsOf as SQLAlchemy's identity_token, so
    the same database row read at two different AsOfs is two objects in one session, each knowing its own AsOf.
    """
    identity_token: object = sqlalchemy.inspect(row).identity_token
    if not isinstance(identity_token, AsOf):
        raise ValueError(f"{type(row).__name__} was not read as of a date and time; re-read it with an as-of query")
    result: AsOf = identity_token
    return result
