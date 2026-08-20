"""Public facade for the split nearest gap diagnostics module."""

from continuous_futures_builder._gap_diagnostics_analysis import _classify_gap as _classify_gap
from continuous_futures_builder._gap_diagnostics_analysis import (
    build_session_gap_rows as build_session_gap_rows,
)
from continuous_futures_builder._gap_diagnostics_analysis import (
    build_session_gap_summary as build_session_gap_summary,
)
from continuous_futures_builder._gap_diagnostics_analysis import (
    session_gap_frame as session_gap_frame,
)
from continuous_futures_builder._gap_diagnostics_core import (
    GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK as GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK,
)
from continuous_futures_builder._gap_diagnostics_core import (
    GAP_EXPECTED_SESSION_BREAK as GAP_EXPECTED_SESSION_BREAK,
)
from continuous_futures_builder._gap_diagnostics_core import (
    GAP_ROLL_TRANSITION as GAP_ROLL_TRANSITION,
)
from continuous_futures_builder._gap_diagnostics_core import (
    GAP_UNEXPECTED_INTRADAY as GAP_UNEXPECTED_INTRADAY,
)
from continuous_futures_builder._gap_diagnostics_core import GAP_UNKNOWN as GAP_UNKNOWN
from continuous_futures_builder._gap_diagnostics_core import (
    IntradaySessionModel as IntradaySessionModel,
)
from continuous_futures_builder._gap_diagnostics_core import (
    SessionGapDiagnosticsResult as SessionGapDiagnosticsResult,
)
from continuous_futures_builder._gap_diagnostics_core import SessionGapRow as SessionGapRow
from continuous_futures_builder._gap_diagnostics_core import SessionGapSummary as SessionGapSummary
from continuous_futures_builder._gap_diagnostics_core import TradingSession as TradingSession
from continuous_futures_builder._gap_diagnostics_core import _as_datetime as _as_datetime
from continuous_futures_builder._gap_diagnostics_core import (
    _is_expected_session_break as _is_expected_session_break,
)
from continuous_futures_builder._gap_diagnostics_core import _near_session_end as _near_session_end
from continuous_futures_builder._gap_diagnostics_core import (
    _near_session_start as _near_session_start,
)
from continuous_futures_builder._gap_diagnostics_core import _unique_string as _unique_string
from continuous_futures_builder._gap_diagnostics_core import (
    default_moex_futures_session_model as default_moex_futures_session_model,
)
from continuous_futures_builder._gap_diagnostics_core import (
    parse_session_options as parse_session_options,
)
from continuous_futures_builder._gap_diagnostics_output import (
    _status_from_payload as _status_from_payload,
)
from continuous_futures_builder._gap_diagnostics_output import (
    diagnose_dataset_session_gaps as diagnose_dataset_session_gaps,
)
from continuous_futures_builder._gap_diagnostics_output import (
    diagnose_session_gaps as diagnose_session_gaps,
)
from continuous_futures_builder._gap_diagnostics_output import (
    session_summary_quality_fields as session_summary_quality_fields,
)
from continuous_futures_builder._gap_diagnostics_output import (
    update_nearest_quality_files as update_nearest_quality_files,
)
from continuous_futures_builder._gap_diagnostics_output import (
    write_session_gap_reports as write_session_gap_reports,
)
