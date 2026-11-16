from decimal import Decimal
from typing import NamedTuple, Optional

from sqlalchemy import orm

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.instrument as instrument_module
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.quote_convention_for_futures_root as quote_convention_for_futures_root
import bullpen_sec_master.quote_convention_for_instrument as quote_convention_for_instrument
import bullpen_sec_master.symbology as symbology_module


# ======================================================================================================================
class QuoteConvention(NamedTuple):
    """
    How a symbology's numbers relate to bullpen's canonical ones. A canonical price is in major units of the
    instrument's currency (dollars, not cents; pounds, not pence), so that notional = price * multiplier * quantity; a
    canonical futures quantity is in contracts. Conversion happens at the boundary: nothing inside bullpen holds a
    vendor price.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    price_factor: Decimal
    quantity_factor: Decimal

    # ------------------------------------------------------------------------------------------------------------------
    def to_canonical_price(self, vendor_price: Decimal) -> Decimal:
        result: Decimal = vendor_price * self.price_factor
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def to_vendor_price(self, canonical_price: Decimal) -> Decimal:
        result: Decimal = canonical_price / self.price_factor
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def to_canonical_quantity(self, vendor_quantity: Decimal) -> Decimal:
        result: Decimal = vendor_quantity * self.quantity_factor
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def to_vendor_quantity(self, canonical_quantity: Decimal) -> Decimal:
        result: Decimal = canonical_quantity / self.quantity_factor
        return result


CANONICAL_QUOTE_CONVENTION: QuoteConvention = QuoteConvention(Decimal(1), Decimal(1))


# ----------------------------------------------------------------------------------------------------------------------
def get_quote_convention(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    instrument: instrument_module.Instrument,
    as_of: as_of_module.AsOf,
) -> QuoteConvention:
    """
    The convention this symbology uses for the instrument. Walk the symbology chain, most specific first; within one
    symbology an instrument's own convention beats its futures root's. With nothing stored, the symbology is canonical.
    """
    result: Optional[QuoteConvention] = None
    for chain_symbology in symbology.get_chain():
        # the instrument's own convention in this symbology
        if result is None:
            instrument_row: Optional[quote_convention_for_instrument.QuoteConventionForInstrument] = (
                quote_convention_for_instrument.QuoteConventionForInstrument.find_as_of(
                    session, chain_symbology, instrument.instrument_id, as_of
                )
            )
            if instrument_row is not None:
                result = QuoteConvention(instrument_row.price_factor, instrument_row.quantity_factor)

        # a futures contract is otherwise quoted like every contract of its root
        if result is None:
            if isinstance(instrument, instrument_futures_contract.InstrumentFuturesContract):
                root_row: Optional[quote_convention_for_futures_root.QuoteConventionForFuturesRoot] = (
                    quote_convention_for_futures_root.QuoteConventionForFuturesRoot.find_as_of(
                        session, chain_symbology, instrument.futures_root_id, as_of
                    )
                )
                if root_row is not None:
                    result = QuoteConvention(root_row.price_factor, root_row.quantity_factor)

    if result is None:
        result = CANONICAL_QUOTE_CONVENTION
    return result
