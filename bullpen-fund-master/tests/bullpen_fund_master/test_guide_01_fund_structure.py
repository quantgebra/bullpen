"""
User guide, part 1: the fund structure.

The fund master says who owns what and who decides what:
  FUND      a legal entity that owns positions (MAIN, OFFSHORE).
  MANDATE   a strategy, run against research weights (ALPHA). A mandate is not owned by a fund.
  SLEEVE    a fund running a mandate: the pair (fund_id, mandate_id). ALPHA may run in MAIN and in OFFSHORE, as two
            sleeves.
  BROKER    a prime broker or custodian (GS).
  ACCOUNT   an account a fund holds at a broker. Any sleeve of the fund may trade through it.
The IBOR's book (fund, mandate, account) is valid exactly when its sleeve exists and the account belongs to its fund.

Everything is bi-temporal, like the securities master: funds launch and close, a fund starts and stops running a
mandate, accounts open and close, and we learn of these late. Reads take an AsOf(effective_date, known_at_datetime);
other packages refer to these entities by stable id only.
"""

from datetime import date, datetime, timezone

import pytest
import sqlalchemy

import bullpen_fund_master.account as account_module
import bullpen_fund_master.fund as fund_module
import bullpen_fund_master.sleeve as sleeve_module
import bullpen_temporal.as_of as as_of_module

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME

NOW_AS_OF: as_of_module.AsOf = as_of_module.AsOf(date(2026, 11, 13), datetime(2026, 11, 15, tzinfo=timezone.utc))


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def main_sleeve_by_mandate_code(session, fund_by_code, mandate_by_code) -> dict[str, sleeve_module.Sleeve]:
    """MAIN runs ALPHA and BETA."""
    result: dict[str, sleeve_module.Sleeve] = {}
    for mandate_code in ["ALPHA", "BETA"]:
        result[mandate_code] = sleeve_module.Sleeve.create(
            session, BEGIN_DATE, FIRST_KNOWN_DATETIME, fund_by_code["MAIN"], mandate_by_code[mandate_code]
        )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def main_gs_account(session, fund_by_code, broker_by_code) -> account_module.Account:
    """MAIN's prime-brokerage account at GS, account number 1234."""
    result: account_module.Account = account_module.Account.create(
        session, BEGIN_DATE, FIRST_KNOWN_DATETIME, fund_by_code["MAIN"], broker_by_code["GS"], "1234", "MAIN at GS"
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_funds_mandates_and_brokers_are_found_by_code(
    session, fund_by_code, mandate_by_code, broker_by_code
) -> None:
    """Each has a stable id and a code; a fund did not exist before it was launched."""
    main_fund: fund_module.Fund = fund_module.Fund.find_by_code_as_of(session, "MAIN", NOW_AS_OF)
    before_launch_fund: fund_module.Fund = fund_module.Fund.find_by_code_as_of(
        session, "MAIN", as_of_module.AsOf(date(2019, 12, 31), NOW_AS_OF.known_at_datetime)
    )

    assert main_fund.fund_id == fund_by_code["MAIN"].fund_id
    assert main_fund.base_currency_code == "USD"
    assert before_launch_fund is None


# ----------------------------------------------------------------------------------------------------------------------
def test_02_a_sleeve_is_a_fund_running_a_mandate(session, fund_by_code, main_sleeve_by_mandate_code) -> None:
    """MAIN's fund structure is its sleeves; from a sleeve you reach its fund and mandate at the same AsOf."""
    main_sleeves: list[sleeve_module.Sleeve] = sleeve_module.Sleeve.find_for_fund_as_of(
        session, fund_by_code["MAIN"], NOW_AS_OF
    )

    assert [sleeve.get_mandate(session).code for sleeve in main_sleeves] == ["ALPHA", "BETA"]
    assert {sleeve.get_fund(session).code for sleeve in main_sleeves} == {"MAIN"}


# ----------------------------------------------------------------------------------------------------------------------
def test_03_one_mandate_can_run_in_several_funds(
    session, fund_by_code, mandate_by_code, main_sleeve_by_mandate_code
) -> None:
    """OFFSHORE starts running ALPHA too: ALPHA now has two sleeves, one per fund."""
    sleeve_module.Sleeve.create(
        session,
        date(2026, 3, 1),
        datetime(2026, 2, 20, tzinfo=timezone.utc),
        fund_by_code["OFFSHORE"],
        mandate_by_code["ALPHA"],
    )

    alpha_sleeves: list[sleeve_module.Sleeve] = sleeve_module.Sleeve.find_for_mandate_as_of(
        session, mandate_by_code["ALPHA"], NOW_AS_OF
    )

    assert [sleeve.fund_id for sleeve in alpha_sleeves] == sorted(
        [fund_by_code["MAIN"].fund_id, fund_by_code["OFFSHORE"].fund_id]
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_04_the_fund_structure_changes_over_time(session, fund_by_code, main_sleeve_by_mandate_code) -> None:
    """
    MAIN stops running BETA from 2026-07-01, and we learn of it on 2026-07-03. In June MAIN ran both; in August only
    ALPHA, except to a run made before we learned of the change.
    """
    believed_beta_sleeve: sleeve_module.Sleeve = sleeve_module.Sleeve.find_as_of(
        session, fund_by_code["MAIN"].fund_id, main_sleeve_by_mandate_code["BETA"].mandate_id, NOW_AS_OF
    )
    believed_beta_sleeve.end(session, date(2026, 7, 1), datetime(2026, 7, 3, tzinfo=timezone.utc))

    june_sleeves: list[sleeve_module.Sleeve] = sleeve_module.Sleeve.find_for_fund_as_of(
        session, fund_by_code["MAIN"], NOW_AS_OF._replace(effective_date=date(2026, 6, 15))
    )
    august_sleeves: list[sleeve_module.Sleeve] = sleeve_module.Sleeve.find_for_fund_as_of(
        session, fund_by_code["MAIN"], NOW_AS_OF._replace(effective_date=date(2026, 8, 3))
    )
    august_before_learning_sleeves: list[sleeve_module.Sleeve] = sleeve_module.Sleeve.find_for_fund_as_of(
        session, fund_by_code["MAIN"], as_of_module.AsOf(date(2026, 8, 3), datetime(2026, 7, 2, tzinfo=timezone.utc))
    )

    assert len(june_sleeves) == 2
    assert [sleeve.mandate_id for sleeve in august_sleeves] == [main_sleeve_by_mandate_code["ALPHA"].mandate_id]
    assert len(august_before_learning_sleeves) == 2


# ----------------------------------------------------------------------------------------------------------------------
def test_05_an_account_belongs_to_a_fund_and_a_broker(session, broker_by_code, main_gs_account) -> None:
    """A broker statement names account 1234 at GS; it resolves to MAIN's account, and from there to MAIN."""
    statement_account: account_module.Account = account_module.Account.find_by_code_as_of(
        session, broker_by_code["GS"], "1234", NOW_AS_OF
    )

    assert statement_account.account_id == main_gs_account.account_id
    assert statement_account.get_fund(session).code == "MAIN"
    assert statement_account.get_broker(session).code == "GS"


# ----------------------------------------------------------------------------------------------------------------------
def test_06_a_book_fits_when_its_sleeve_exists_and_the_account_is_its_funds(
    session, fund_by_code, broker_by_code, main_sleeve_by_mandate_code, main_gs_account
) -> None:
    """
    Before the IBOR books (MAIN, ALPHA, account), the command handler checks the fund master at the trade's AsOf: the
    sleeve must exist, and the sleeve must be able to trade through the account. OFFSHORE's account does not fit.
    """
    offshore_gs_account: account_module.Account = account_module.Account.create(
        session, BEGIN_DATE, FIRST_KNOWN_DATETIME, fund_by_code["OFFSHORE"], broker_by_code["GS"], "5678", "OFF at GS"
    )

    alpha_sleeve: sleeve_module.Sleeve = sleeve_module.Sleeve.find_as_of(
        session, fund_by_code["MAIN"].fund_id, main_sleeve_by_mandate_code["ALPHA"].mandate_id, NOW_AS_OF
    )

    assert alpha_sleeve.can_trade_through(
        account_module.Account.find_as_of(session, main_gs_account.account_id, NOW_AS_OF)
    )
    assert not alpha_sleeve.can_trade_through(
        account_module.Account.find_as_of(session, offshore_gs_account.account_id, NOW_AS_OF)
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_07_a_renamed_fund_keeps_its_id_and_its_history(session, fund_by_code) -> None:
    """MAIN is renamed from 2026-09-01; earlier dates keep the old name, and the fund_id never changes."""
    renamed_fund: fund_module.Fund = fund_by_code["MAIN"].with_new_name(
        session, date(2026, 9, 1), datetime(2026, 9, 1, tzinfo=timezone.utc), "Main Fund II LP"
    )

    august_fund: fund_module.Fund = fund_module.Fund.find_as_of(
        session, renamed_fund.fund_id, NOW_AS_OF._replace(effective_date=date(2026, 8, 3))
    )
    november_fund: fund_module.Fund = fund_module.Fund.find_as_of(session, renamed_fund.fund_id, NOW_AS_OF)

    assert renamed_fund.fund_id == fund_by_code["MAIN"].fund_id
    assert august_fund.name == "Main Fund LP"
    assert november_fund.name == "Main Fund II LP"


# ----------------------------------------------------------------------------------------------------------------------
def test_08_only_the_latest_believed_row_can_change(session, fund_by_code) -> None:
    """After a rename, the old row is a superseded belief: changing it again would lose the rename, so it raises."""
    fund_by_code["MAIN"].with_new_name(session, date(2026, 9, 1), datetime(2026, 9, 1, tzinfo=timezone.utc), "Renamed")

    with pytest.raises(ValueError):
        fund_by_code["MAIN"].with_new_name(
            session, date(2026, 10, 1), datetime(2026, 10, 1, tzinfo=timezone.utc), "Renamed Again"
        )


# ----------------------------------------------------------------------------------------------------------------------
def test_09_a_code_names_one_fund_at_a_time(session, fund_by_code) -> None:
    """Two funds cannot share a code over the same dates and beliefs; the database refuses the second."""
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        fund_module.Fund.create(session, date(2026, 1, 1), FIRST_KNOWN_DATETIME, "MAIN", "Another Main", "EUR")
