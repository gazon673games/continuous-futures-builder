class ContinuousFuturesError(Exception):
    pass


class ContractChainError(ContinuousFuturesError):
    pass


class RollCalendarError(ContinuousFuturesError):
    pass


class AdjustmentError(ContinuousFuturesError):
    pass


class ContinuousSeriesValidationError(ContinuousFuturesError):
    pass


class InputDataError(ContinuousFuturesError):
    pass
