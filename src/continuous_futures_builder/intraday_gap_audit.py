"""Public facade for the split intraday gap audit module."""

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
from continuous_futures_builder._gap_audit_coverage import (
    build_decision_time_coverage as build_decision_time_coverage,
)
from continuous_futures_builder._gap_audit_report import (
    build_markdown_report as build_markdown_report,
)
from continuous_futures_builder._gap_audit_report import (
    write_gap_audit_reports as write_gap_audit_reports,
)
from continuous_futures_builder._gap_audit_summary import build_audit_summary as build_audit_summary
