from __future__ import annotations

DERIVED_SOURCE = "continuous_futures_builder"

DEFAULT_EXCHANGE = "MOEX"
DEFAULT_ENGINE = "futures"
DEFAULT_MARKET = "forts"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_CONTINUOUS_SYMBOL = "CNYRUB_CONT"
DEFAULT_ROLL_RULE = "fixed_days_before_expiration"
DEFAULT_ROLL_DAYS_BEFORE = 3

ADJUSTMENT_UNADJUSTED = "unadjusted"
ADJUSTMENT_BACK_ADJUSTED = "back_adjusted"
SUPPORTED_ADJUSTMENT_MODES = {ADJUSTMENT_UNADJUSTED, ADJUSTMENT_BACK_ADJUSTED}
SUPPORTED_ROLL_RULES = {DEFAULT_ROLL_RULE}

PRICE_COLUMNS = ("open", "high", "low", "close")

INPUT_CANDLE_COLUMNS = (
    "source",
    "exchange",
    "engine",
    "market",
    "board",
    "symbol",
    "timeframe",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "currency",
)

INPUT_METADATA_COLUMNS = (
    "source",
    "exchange",
    "engine",
    "market",
    "board",
    "secid",
    "shortname",
    "secname",
    "assetcode",
    "instrument_type",
    "last_trade_date",
    "expiration_date",
    "currency",
    "lot_size",
    "raw_metadata",
    "snapshot_ts",
)

OUTPUT_COLUMNS = (
    "source",
    "exchange",
    "engine",
    "market",
    "continuous_symbol",
    "timeframe",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "currency",
    "active_contract",
    "active_board",
    "source_contract_symbol",
    "roll_id",
    "roll_flag",
    "roll_date",
    "adjustment_mode",
    "adjustment_factor",
)
