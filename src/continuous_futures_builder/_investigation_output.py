from __future__ import annotations

import json
from pathlib import Path

from continuous_futures_builder._investigation_core import (
    RollInvestigation,
)
from continuous_futures_builder.models import (
    RollInvestigationSaveResult,
)


def save_roll_investigation(
    investigation: RollInvestigation,
    output_dir: str | Path,
) -> RollInvestigationSaveResult:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    summary_path = target_dir / "investigation_summary.json"
    continuous_path = target_dir / "continuous_window.csv"
    from_path = target_dir / "from_contract_window.csv"
    to_path = target_dir / "to_contract_window.csv"
    candidates_path = target_dir / "candidate_contracts.csv"

    investigation.continuous_window.write_csv(continuous_path)
    investigation.from_contract_window.write_csv(from_path)
    investigation.to_contract_window.write_csv(to_path)
    investigation.candidate_contracts_frame.write_csv(candidates_path)

    save_result = RollInvestigationSaveResult(
        output_dir=target_dir,
        investigation_summary_json=summary_path,
        continuous_window_csv=continuous_path,
        from_contract_window_csv=from_path,
        to_contract_window_csv=to_path,
        candidate_contracts_csv=candidates_path,
    )
    investigation.summary = investigation.summary.model_copy(update={"save_result": save_result})
    summary_path.write_text(
        json.dumps(investigation.summary.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return save_result
