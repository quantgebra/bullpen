import enum


# ======================================================================================================================
class InstrumentType(str, enum.Enum):

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    CASH = "CASH"
    EQUITY = "EQUITY"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    INDEX = "INDEX"
    BOND = "BOND"
    SYNTHETIC = "SYNTHETIC"

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    # ------------------------------------------------------------------------------------------------------------------
    def requires_tax_lots(self) -> bool:
        lot_types: tuple[InstrumentType, ...] = (
            InstrumentType.EQUITY,
            InstrumentType.ETF,
            InstrumentType.MUTUAL_FUND,
            InstrumentType.BOND,
        )
        result: bool = self in lot_types
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def uses_variation_margin(self) -> bool:
        result: bool = self is InstrumentType.FUTURE
        return result
