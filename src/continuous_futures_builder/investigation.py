"""Public facade for roll investigation workflows."""

from continuous_futures_builder._investigation_core import (
    CANDIDATE_COLUMNS,
    CANDLE_WINDOW_COLUMNS,
    CONTINUOUS_WINDOW_COLUMNS,
    RollInvestigation,
)
from continuous_futures_builder._investigation_gaps import (
    detect_structural_gap,
)
from continuous_futures_builder._investigation_output import save_roll_investigation
from continuous_futures_builder._investigation_policy import (
    classify_roll_outlier,
)
from continuous_futures_builder._investigation_runner import investigate_roll

__all__ = [
    "RollInvestigation",
    "CANDIDATE_COLUMNS",
    "CANDLE_WINDOW_COLUMNS",
    "CONTINUOUS_WINDOW_COLUMNS",
    "classify_roll_outlier",
    "detect_structural_gap",
    "investigate_roll",
    "save_roll_investigation",
]
