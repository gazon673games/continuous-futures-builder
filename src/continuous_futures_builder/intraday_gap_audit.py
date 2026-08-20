"""Public facade for the split intraday gap audit module."""

from continuous_futures_builder._gap_audit_contracts import (
    _decision_counts_by_contract_month as _decision_counts_by_contract_month,
)
from continuous_futures_builder._gap_audit_contracts import (
    _first_trading_date_after as _first_trading_date_after,
)
from continuous_futures_builder._gap_audit_contracts import (
    _first_trading_date_after_month as _first_trading_date_after_month,
)
from continuous_futures_builder._gap_audit_contracts import (
    _last_excluded_month as _last_excluded_month,
)
from continuous_futures_builder._gap_audit_contracts import (
    _month_recommendation as _month_recommendation,
)
from continuous_futures_builder._gap_audit_contracts import (
    build_contract_month_audit as build_contract_month_audit,
)
from continuous_futures_builder._gap_audit_contracts import (
    first_date_with_consecutive_usable_days as first_date_with_consecutive_usable_days,
)
from continuous_futures_builder._gap_audit_core import (
    REASON_GAP_OVERLAPS_WINDOW as REASON_GAP_OVERLAPS_WINDOW,
)
from continuous_futures_builder._gap_audit_core import (
    REASON_MISSING_DECISION_BAR as REASON_MISSING_DECISION_BAR,
)
from continuous_futures_builder._gap_audit_core import (
    REASON_NOT_ENOUGH_BARS as REASON_NOT_ENOUGH_BARS,
)
from continuous_futures_builder._gap_audit_core import REASON_OK as REASON_OK
from continuous_futures_builder._gap_audit_core import (
    REASON_UNKNOWN_GAP_OVERLAPS_WINDOW as REASON_UNKNOWN_GAP_OVERLAPS_WINDOW,
)
from continuous_futures_builder._gap_audit_core import (
    RECOMMENDATION_EXCLUDE_CONTRACT as RECOMMENDATION_EXCLUDE_CONTRACT,
)
from continuous_futures_builder._gap_audit_core import (
    RECOMMENDATION_EXCLUDE_MONTH as RECOMMENDATION_EXCLUDE_MONTH,
)
from continuous_futures_builder._gap_audit_core import (
    RECOMMENDATION_USABLE as RECOMMENDATION_USABLE,
)
from continuous_futures_builder._gap_audit_core import (
    RECOMMENDATION_WARNING as RECOMMENDATION_WARNING,
)
from continuous_futures_builder._gap_audit_core import (
    IntradayGapAuditConfig as IntradayGapAuditConfig,
)
from continuous_futures_builder._gap_audit_core import (
    IntradayGapAuditResult as IntradayGapAuditResult,
)
from continuous_futures_builder._gap_audit_core import _as_date as _as_date
from continuous_futures_builder._gap_audit_core import _as_datetime as _as_datetime
from continuous_futures_builder._gap_audit_core import _bad_gap_records as _bad_gap_records
from continuous_futures_builder._gap_audit_core import _find_decision_row as _find_decision_row
from continuous_futures_builder._gap_audit_core import _gap_overlaps_window as _gap_overlaps_window
from continuous_futures_builder._gap_audit_core import _load_dataset as _load_dataset
from continuous_futures_builder._gap_audit_core import (
    _load_or_compute_session_gaps as _load_or_compute_session_gaps,
)
from continuous_futures_builder._gap_audit_core import _month_end as _month_end
from continuous_futures_builder._gap_audit_core import _month_key as _month_key
from continuous_futures_builder._gap_audit_core import (
    _rows_by_trading_date as _rows_by_trading_date,
)
from continuous_futures_builder._gap_audit_coverage import (
    _active_contract_for_day as _active_contract_for_day,
)
from continuous_futures_builder._gap_audit_coverage import (
    _base_contract_month_rows as _base_contract_month_rows,
)
from continuous_futures_builder._gap_audit_coverage import (
    _gap_summary_by_contract_month as _gap_summary_by_contract_month,
)
from continuous_futures_builder._gap_audit_coverage import (
    build_decision_time_coverage as build_decision_time_coverage,
)
from continuous_futures_builder._gap_audit_report import (
    build_markdown_report as build_markdown_report,
)
from continuous_futures_builder._gap_audit_report import (
    write_gap_audit_reports as write_gap_audit_reports,
)
from continuous_futures_builder._gap_audit_summary import _json_default as _json_default
from continuous_futures_builder._gap_audit_summary import (
    _last_bad_gap_near_decision_date as _last_bad_gap_near_decision_date,
)
from continuous_futures_builder._gap_audit_summary import _markdown_table as _markdown_table
from continuous_futures_builder._gap_audit_summary import _pct as _pct
from continuous_futures_builder._gap_audit_summary import _single_value as _single_value
from continuous_futures_builder._gap_audit_summary import build_audit_summary as build_audit_summary
