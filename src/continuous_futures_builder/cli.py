"""Typer application facade; command groups register on import."""

from continuous_futures_builder import _cli_artifacts as _cli_artifacts
from continuous_futures_builder import _cli_build as _cli_build
from continuous_futures_builder import _cli_chain as _cli_chain
from continuous_futures_builder import _cli_quality as _cli_quality
from continuous_futures_builder import _cli_reports as _cli_reports
from continuous_futures_builder._cli_core import app as app

__all__ = ["app"]
