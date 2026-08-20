"""Public facade for nearest-contract construction and reporting."""

from continuous_futures_builder._nearest_contract_artifacts import (
    NearestContractFuturesBuilder as NearestContractFuturesBuilder,
)
from continuous_futures_builder._nearest_contract_artifacts import _load_candles as _load_candles
from continuous_futures_builder._nearest_contract_artifacts import _load_chain as _load_chain
from continuous_futures_builder._nearest_contract_artifacts import (
    _save_artifacts as _save_artifacts,
)
from continuous_futures_builder._nearest_contract_build import (
    build_nearest_contract_dataset as build_nearest_contract_dataset,
)
from continuous_futures_builder._nearest_contract_models import (
    LARGE_ROLL_GAP_PERCENT as LARGE_ROLL_GAP_PERCENT,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_DATASET_TYPE as NEAREST_DATASET_TYPE,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_FUTURES_DATASET_DIR as NEAREST_FUTURES_DATASET_DIR,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_OUTPUT_COLUMNS as NEAREST_OUTPUT_COLUMNS,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_QUALITY_OK as NEAREST_QUALITY_OK,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_QUALITY_REJECTED as NEAREST_QUALITY_REJECTED,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_QUALITY_WARNING as NEAREST_QUALITY_WARNING,
)
from continuous_futures_builder._nearest_contract_models import (
    SOURCE_COVERAGE_GAP_WARNING_MINUTES as SOURCE_COVERAGE_GAP_WARNING_MINUTES,
)
from continuous_futures_builder._nearest_contract_models import (
    NearestContractBuildConfig as NearestContractBuildConfig,
)
from continuous_futures_builder._nearest_contract_models import (
    NearestContractBuildResult as NearestContractBuildResult,
)
from continuous_futures_builder._nearest_contract_models import (
    NearestContractQuality as NearestContractQuality,
)
from continuous_futures_builder._nearest_contract_models import (
    NearestRollBoundary as NearestRollBoundary,
)
from continuous_futures_builder._nearest_contract_models import _as_datetime as _as_datetime
from continuous_futures_builder._nearest_contract_models import _date_from_value as _date_from_value
from continuous_futures_builder._nearest_contract_models import _datetime_like as _datetime_like
from continuous_futures_builder._nearest_contract_models import _norm as _norm
from continuous_futures_builder._nearest_contract_quality import _build_quality as _build_quality
from continuous_futures_builder._nearest_contract_quality import _coverage_frame as _coverage_frame
from continuous_futures_builder._nearest_contract_quality import (
    _dataset_output_path as _dataset_output_path,
)
from continuous_futures_builder._nearest_contract_quality import _reports_dir as _reports_dir
from continuous_futures_builder._nearest_contract_quality import _write_json as _write_json
from continuous_futures_builder._nearest_contract_quality import _write_output as _write_output
from continuous_futures_builder._nearest_contract_rolls import (
    _frame_ts_min_max as _frame_ts_min_max,
)
from continuous_futures_builder._nearest_contract_rolls import _latest_snapshot as _latest_snapshot
from continuous_futures_builder._nearest_contract_rolls import (
    _matches_asset_code as _matches_asset_code,
)
from continuous_futures_builder._nearest_contract_rolls import _minutes_between as _minutes_between
from continuous_futures_builder._nearest_contract_rolls import _price_gap_pct as _price_gap_pct
from continuous_futures_builder._nearest_contract_rolls import (
    _raise_for_missing_expiration_metadata as _raise_for_missing_expiration_metadata,
)
from continuous_futures_builder._nearest_contract_rolls import (
    _roll_calendar_frame as _roll_calendar_frame,
)
from continuous_futures_builder._nearest_contract_rolls import _roll_warning as _roll_warning
from continuous_futures_builder._nearest_contract_rolls import (
    _row_first_on_or_after as _row_first_on_or_after,
)
from continuous_futures_builder._nearest_contract_rolls import _row_last_before as _row_last_before
from continuous_futures_builder._nearest_contract_rolls import (
    _timeframe_minutes as _timeframe_minutes,
)
from continuous_futures_builder._nearest_contract_series import (
    _build_roll_calendar as _build_roll_calendar,
)
from continuous_futures_builder._nearest_contract_series import (
    _duplicate_ts_count as _duplicate_ts_count,
)
from continuous_futures_builder._nearest_contract_series import _filter_segment as _filter_segment
from continuous_futures_builder._nearest_contract_series import (
    _intraday_gap_stats as _intraday_gap_stats,
)
from continuous_futures_builder._nearest_contract_series import (
    _null_price_count as _null_price_count,
)
from continuous_futures_builder._nearest_contract_series import _segment_bounds as _segment_bounds
from continuous_futures_builder._nearest_contract_series import (
    _stitch_nearest_contracts as _stitch_nearest_contracts,
)
from continuous_futures_builder._nearest_contract_series import (
    validate_nearest_contract_schema as validate_nearest_contract_schema,
)
