"""
User guide, part 7: reading as of a date and time.

Every read of bi-temporal data asks two questions at once: true on which date (D), and as believed when (K). The pair is
an AsOf. Entry-point reads take an AsOf; the rows they return REMEMBER it, and anything reached from such a row (a
contract's root, a synthetic's legs, a symbol's target) is read at that same AsOf without being told again.

The row remembers its AsOf through SQLAlchemy's identity map: as-of reads load rows with the AsOf as the identity_token,
so one database row read at two AsOfs is two Python objects in one session, each with its own AsOf. That keeps one
session (one transaction, one commit or rollback) per command, however many dates the command looks at.

Rows you just wrote were not read as of anything, so they have no AsOf: re-read them before traversing.
"""

from datetime import date
from decimal import Decimal

import pytest

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.instrument as instrument

from .conftest import FIRST_KNOWN_DATETIME, SECOND_KNOWN_DATETIME, THIRD_KNOWN_DATETIME

ASKED_DATE: date = date(2026, 3, 1)
EARLY_AS_OF: as_of_module.AsOf = as_of_module.AsOf(ASKED_DATE, FIRST_KNOWN_DATETIME)
LATE_AS_OF: as_of_module.AsOf = as_of_module.AsOf(ASKED_DATE, THIRD_KNOWN_DATETIME)


# ----------------------------------------------------------------------------------------------------------------------
def test_01_one_row_read_at_two_as_ofs_is_two_objects_in_one_session(session, clz6_future_contract) -> None:
    """The same CLZ6 row, read early and late, comes back as two objects; each knows the AsOf it was read at."""
    early_contract = instrument.Instrument.find_as_of(session, clz6_future_contract.instrument_id, EARLY_AS_OF)
    late_contract = instrument.Instrument.find_as_of(session, clz6_future_contract.instrument_id, LATE_AS_OF)

    assert early_contract.row_id == late_contract.row_id
    assert early_contract is not late_contract
    assert as_of_module.get_as_of_from_row(early_contract) == EARLY_AS_OF
    assert as_of_module.get_as_of_from_row(late_contract) == LATE_AS_OF


# ----------------------------------------------------------------------------------------------------------------------
def test_02_traversal_reuses_the_rows_as_of(session, clz6_future_contract, futures_root_by_code) -> None:
    """
    CL's tick size is corrected (learned at SECOND_KNOWN_DATETIME). From the early contract we reach the root as it was
    believed early; from the late contract, as believed late. Neither traversal is told a date or a time.
    """
    futures_root_by_code["CL"].with_new_tick_size(session, date(2026, 1, 1), SECOND_KNOWN_DATETIME, Decimal("0.02"))
    early_contract = instrument.Instrument.find_as_of(session, clz6_future_contract.instrument_id, EARLY_AS_OF)
    late_contract = instrument.Instrument.find_as_of(session, clz6_future_contract.instrument_id, LATE_AS_OF)

    early_root = early_contract.get_root(session)
    late_root = late_contract.get_root(session)

    assert early_root.tick_size == Decimal("0.01")
    assert late_root.tick_size == Decimal("0.02")
    assert as_of_module.get_as_of_from_row(early_root) == EARLY_AS_OF


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_row_you_just_wrote_has_no_as_of(session, clz6_future_contract) -> None:
    """create() returns the row it wrote, which was never read as of anything; traversing from it is refused."""
    with pytest.raises(ValueError):
        clz6_future_contract.get_root(session)

    re_read_contract = instrument.Instrument.find_as_of(session, clz6_future_contract.instrument_id, LATE_AS_OF)

    assert re_read_contract.get_root(session).name == "CL"
