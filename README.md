# continuous-futures-builder

This project builds derived continuous futures datasets from local normalized data. It does not download market data and does not run backtests.

`continuous-futures-builder` is a standalone research-data builder for stitching concrete futures contracts into traceable continuous futures series. The MVP targets MOEX CNY/RUB-like futures with daily candles, fixed-days-before-expiration rolls, and two output modes: `unadjusted` and `back_adjusted`.

## What It Does

- Reads local normalized futures candles and metadata snapshots.
- Builds a configurable contract chain from metadata and, when explicitly
  filtered by one or more SECID prefixes or include list, can augment missing historical
  metadata from existing candle partitions.
- Supports multi-family chains such as `CY*` plus `CR*` without hardcoding
  those families in code.
- Builds a fixed-days-before-expiration roll calendar.
- Stitches concrete contracts into a continuous series.
- Preserves `active_contract` and `source_contract_symbol` on every output row.
- Reports family/prefix transitions and roll gaps so family changes stay
  visible in research outputs.
- Reports sparse-contract pruning explicitly in build output.
- Produces compact quality and roll diagnostics without printing full
  DataFrames.
- Applies a policy-based quality gate that marks generated datasets as
  `approved`, `rejected`, or `split_required`.
- Investigates individual roll outliers with source candle windows,
  candidate/pruned contract summaries, and diagnostic structural-gap flags.
- Writes derived Parquet under `data_root/derived/continuous_futures/...`.

## What It Does Not Do

- It does not import `moex_iss_adapter`.
- It does not call MOEX ISS or any market-data API.
- It does not normalize raw exchange responses.
- It does not run backtests, strategies, PnL, execution, visualization, web APIs, or database infrastructure.

## Relationship To Other Projects

`moex_iss_adapter` is responsible for downloading and normalizing concrete contract data. This project only reads already-normalized local data.

`ohlcv-data-store` is the intended stable read layer. Production reads prefer the public `ohlcv_data_store.DataStore` API when it is installed. The small local normalized-layout reader remains only as a fallback for deterministic fixtures and simple local use.

## Setup

Basic development setup:

```bash
uv sync
```

Install the sibling data-store package editably into the current environment:

```bash
uv pip install -e ../data_store
```

If `ohlcv_data_store` is not installed, the builder uses the local fallback reader by default. To require the public data-store API, construct `ContinuousFuturesBuilder(..., allow_local_fallback=False)`.

## Input Layout

The local reader expects normalized candles:

```text
normalized/candles/exchange=MOEX/engine=futures/market=forts/board=RFUD/symbol=CRH4/timeframe=1d/part-000.parquet
```

And metadata snapshots:

```text
normalized/metadata/exchange=MOEX/engine=futures/market=forts/securities/snapshot_ts=20240101T000000Z/part-000.parquet
```

Metadata must provide `secid`, `board`, and either `expiration_date` or `last_trade_date`. Contracts missing both dates are skipped with an explicit warning.

## Output Layout

Derived output is written separately from adapter-normalized candles:

```text
derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

The writer refuses to write into `normalized/candles`.

When input data lives in a sibling adapter directory, pass `--output-root data`
to keep derived output inside this repository:

```text
data/derived/continuous_futures/...
```

## Output Schema

Required output columns:

```text
source, exchange, engine, market, continuous_symbol, timeframe, ts,
open, high, low, close, volume, value, currency,
active_contract, active_board, source_contract_symbol,
roll_id, roll_flag, roll_date, adjustment_mode, adjustment_factor
```

`active_contract` and `source_contract_symbol` are non-null on every row so future research or backtests can map the continuous row back to an executable contract.
The continuous symbol itself is not an executable exchange contract. It is a
derived research series; `active_contract` tells you which concrete futures
contract supplied each row.

## Python API

```python
from continuous_futures_builder import ContinuousFuturesBuilder

builder = ContinuousFuturesBuilder(data_root="tests/fixtures/data")
result = builder.build(
    exchange="MOEX",
    engine="futures",
    market="forts",
    continuous_symbol="CNYRUB_CONT",
    timeframe="1d",
    metadata_snapshot_ts="20240101T000000Z",
    query="CR",
    secid_prefixes=["CR"],
    roll_rule="fixed_days_before_expiration",
    roll_days_before=3,
    adjustment_mode="back_adjusted",
    save=True,
    overwrite=True,
)
```

Preview the chain:

```python
chain = builder.build_chain(
    exchange="MOEX",
    engine="futures",
    market="forts",
    metadata_snapshot_ts="20240101T000000Z",
    query="CR",
    secid_prefixes=["CR"],
)
```

## CLI

List available futures metadata snapshots:

```bash
uv run continuous-futures snapshots \
  --data-root tests/fixtures/data \
  --exchange MOEX \
  --engine futures \
  --market forts
```

```bash
uv run continuous-futures chain \
  --data-root tests/fixtures/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts 20240101T000000Z \
  --query CR \
  --secid-prefix CR
```

```bash
uv run continuous-futures build \
  --data-root tests/fixtures/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --continuous-symbol CNYRUB_CONT \
  --timeframe 1d \
  --metadata-snapshot-ts 20240101T000000Z \
  --query CR \
  --secid-prefix CR \
  --roll-rule fixed_days_before_expiration \
  --roll-days-before 3 \
  --adjustment-mode back_adjusted \
  --save \
  --overwrite
```

```bash
uv run continuous-futures validate \
  --path tests/fixtures/data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

```bash
uv run continuous-futures quality \
  --path tests/fixtures/data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

```bash
uv run continuous-futures quality-gate \
  --path tests/fixtures/data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

```bash
uv run continuous-futures roll-report \
  --path tests/fixtures/data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet \
  --top 10
```

## Nearest-contract intraday datasets

`CNYRUB_NEAREST_5M` is a derived nearest-contract intraday dataset for local
CNY/RUB futures research. It is not a back-adjusted continuous series: each
row keeps the real, unadjusted OHLCV price from the concrete active contract.
The dataset is meant for workflows that need one tradable-looking intraday
stream while still preserving the executable source contract on every row.

The builder consumes already-normalized 5-minute concrete futures candles:

```text
../moex_iss_adapter/data/normalized/candles/exchange=MOEX/engine=futures/market=forts/board=RFUD/symbol=<SECID>/timeframe=5m/part-000.parquet
```

Those 5-minute candles are treated as validated input. In the local adapter
workflow they were derived upstream by `moex_iss_adapter` from MOEX 1-minute
bars; this builder does not download MOEX data and does not re-aggregate 1m
bars into 5m bars.

Build the CR-family CNY/RUB nearest-contract dataset:

```bash
uv run continuous-futures nearest build \
  --data-root ../moex_iss_adapter/data \
  --output-root data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --board RFUD \
  --secid-prefix CR \
  --asset-code CNY \
  --timeframe 5m \
  --continuous-symbol CNYRUB_NEAREST_5M \
  --adjustment-mode unadjusted \
  --roll-days-before-expiration 3
```

The command chooses the latest metadata snapshot unless
`--metadata-snapshot-ts` is provided. Historical contracts that are absent from
a current snapshot may be augmented from existing candle partitions using the
same documented local fallback as the daily continuous builder; contracts with
invalid or empty expiration metadata are rejected.

Output is written under:

```text
data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet
```

Required traceability columns include:

```text
source, exchange, engine, market, board, symbol, continuous_symbol, timeframe,
ts, open, high, low, close, volume, value, currency, active_contract,
source_contract_symbol, roll_id, is_roll_boundary, dataset_type,
adjustment_mode
```

`symbol` and `continuous_symbol` are both `CNYRUB_NEAREST_5M`.
`active_contract` and `source_contract_symbol` hold the concrete SECID that
supplied the row. `dataset_type` is `nearest_contract`, and
`adjustment_mode` is always `unadjusted`.

Rolls are date-based. For a contract expiring on date `D`, the target roll date
is `D - --roll-days-before-expiration` calendar days. If that date has candles
in the next contract, all rows from that date onward use the next contract. If
the target date has no rows, the switch happens at the first available
timestamp after the target date.

Diagnostics are written under:

```text
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/
```

Artifacts:

```text
build_summary.json
roll_calendar.csv
roll_calendar.parquet
session_gaps.csv
session_gaps.parquet
session_gap_summary.json
quality.json
contract_coverage.csv
contract_coverage.parquet
```

`quality.json` reports row counts, min/max timestamps, duplicate timestamps,
null OHLC/contract counts, legacy large intraday gaps, session-aware gap
counts, roll boundaries, contracts used, contracts skipped, and status: `ok`,
`warning`, or `rejected`.

Naive intraday gap counts are misleading because regular breaks between
sessions, overnight breaks, weekends, and holidays can all look like missing
5-minute bars. Nearest-contract diagnostics therefore use a pragmatic default
MOEX futures session model, interpreted in the local timestamps stored in the
dataset:

```text
main=10:00-18:45
evening=19:00-23:50
```

Session-aware gaps are classified as:

```text
expected_session_break
expected_overnight_or_calendar_break
roll_transition_gap
unexpected_intraday_gap
unknown_gap
```

Quality status is based on duplicate timestamps, null OHLC values, null active
contracts, zero-row output, missing required metadata, unexpected intraday
gaps, unknown gaps, and roll/source-coverage warnings. Normal session breaks,
overnights, weekends, and holiday-like calendar breaks do not make a dataset
rejected.

Rerun only the session-aware diagnostics for an existing saved dataset:

```bash
uv run continuous-futures nearest diagnose-gaps \
  --dataset-path data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet \
  --report-root data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M \
  --bar-minutes 5
```

Override the default sessions with repeated `--session` values when needed:

```bash
uv run continuous-futures nearest diagnose-gaps \
  --dataset-path <PARQUET> \
  --report-root <REPORT_DIR> \
  --bar-minutes 5 \
  --session main=10:00-18:45 \
  --session evening=19:00-23:50
```

Session-aware gap diagnostics explain timestamp gaps. They do not answer
whether a specific strategy can safely read the dataset. For the first planned
5-minute research pass, use the decision-time audit:

```bash
uv run continuous-futures nearest audit-gaps \
  --dataset-path data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet \
  --session-gaps-path data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/session_gaps.parquet \
  --report-root data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit \
  --decision-time 13:30 \
  --decision-tolerance-minutes 5 \
  --sma-window-bars 42
```

The audit checks one row per trading date. A day is usable when a bar exists at
or immediately before the decision time within tolerance, at least 42 same-day
5-minute bars are available through that decision bar, and no unexpected or
unknown session gap overlaps the SMA window. It writes:

```text
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/gap_audit_summary.json
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/gap_audit_by_contract_month.csv
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/gap_audit_by_contract_month.parquet
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/decision_time_coverage.csv
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/decision_time_coverage.parquet
data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/gap_audit_report.md
```

Convert a passing audit into the formal approval artifact consumed by `backtest_engine`:

```bash
uv run continuous-futures nearest approve-audit \
  --gap-audit-summary-path data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/gap_audit_summary.json \
  --dataset-path data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet \
  --output-path data/reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/quality_approval.json \
  --min-usable-decision-day-pct 0.95 \
  --allowed-decision-time 13:30 \
  --allowed-sma-window-bars 42
```

The approval stores status, dataset hash, audit hash, approved start date, allowed decision times,
allowed SMA windows, and rejection reasons if thresholds are not met. The command exits non-zero
when the approval is rejected.

Build artifacts consumed by `backtest_engine`:

```bash
uv run continuous-futures prepare-backtest-artifacts \
  --metadata-snapshot-path ../moex_iss_adapter/data/normalized/metadata/exchange=MOEX/engine=futures/market=forts/securities/snapshot_ts=<SNAPSHOT_TS> \
  --observed-dataset-path ../continuous_futures_builder/data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet \
  --derived-root ../continuous_futures_builder/data/derived \
  --reports-root ../continuous_futures_builder/data/reports \
  --output-dir ../backtest_engine/configs/generated \
  --symbol-prefix CR \
  --symbol-prefix Si
```

The one command above writes the registry, observed calendar, registry audit, calendar audit,
data catalog, and `backtest_artifacts_manifest.json`. The lower-level commands are available when
you need to rebuild one artifact:

```bash
uv run continuous-futures build-instrument-registry \
  --metadata-snapshot-path ../moex_iss_adapter/data/normalized/metadata/exchange=MOEX/engine=futures/market=forts/securities/snapshot_ts=<SNAPSHOT_TS> \
  --output-path ../backtest_engine/configs/generated/moex_forts_instrument_specs.json \
  --symbol-prefix CR \
  --symbol-prefix Si

uv run continuous-futures build-observed-calendar \
  --dataset-path data/derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted/part-000.parquet \
  --output-path ../backtest_engine/configs/generated/cnyrub_nearest_5m_observed_calendar.json

uv run continuous-futures build-data-catalog \
  --derived-root ../continuous_futures_builder/data/derived \
  --reports-root ../continuous_futures_builder/data/reports \
  --output-path ../backtest_engine/configs/generated/data_catalog.json \
  --instrument-registry-path configs/generated/moex_forts_instrument_specs.json \
  --calendar-path configs/generated/cnyrub_nearest_5m_observed_calendar.json
```

The instrument registry is built from local MOEX normalized metadata fields such as `minstep`,
`stepprice`, `lotvolume`, and `initialmargin`. The calendar is an observed calendar inferred from
dataset timestamps, not an official exchange holiday source.

The audit report recommends a conservative research start date from the later of the
first 30 consecutive usable decision days and the first trading date after the
last excluded contract/month period. The report itself does not repair data,
approve a dataset for production backtesting, use an official exchange holiday calendar, or model
tradeability, slippage, or execution. It only evaluates close/bar availability
for the planned 13:30 decision workflow.

Limitations: the MVP is CR-family focused, supports only unadjusted prices,
uses a fixed calendar-days-before-expiration roll rule, does not use an
official holiday calendar, and does not change the existing backtest
quality-gate manifest. Gap diagnostics do not modify prices and do not prove
exchange completeness; they only separate likely normal market-session breaks
from gaps that need closer inspection.

## Real Local CNY/RUB Workflow

Manual workflow:

1. Use `moex_iss_adapter` to download futures metadata and concrete futures candles.
2. Use `ohlcv-data-store` to validate the local adapter output.
3. Use `continuous-futures-builder` to build the derived continuous series.

Find metadata snapshots in real local adapter output:

```bash
uv run continuous-futures snapshots \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts
```

Preview the CNY/RUB-like chain:

```bash
uv run continuous-futures chain \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --secid-prefix CY \
  --secid-prefix CR
```

The `chain` command prints selected SECIDs plus diagnostics per contract:
`prefix`, `assetcode`, expiration dates, candle row count, and min/max timestamps. If
`--query CNY` mixes contract families such as `CR*`, `MY*`, `UC*`, and
`CNYRUBF`, narrow it before building with explicit prefixes:

```bash
uv run continuous-futures chain \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --secid-prefix CY \
  --secid-prefix CR
```

Repeated `--secid-prefix` is preferred. A comma-separated form is also
accepted for shell convenience:

```bash
uv run continuous-futures chain \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --secid-prefixes CY,CR
```

When a metadata snapshot contains only current/future contracts but normalized
candle partitions already contain historical SECIDs, explicit
`--secid-prefix` values allow the builder to augment missing historical
contracts from matching candle partitions. For augmented contracts, expiration
and last-trade dates are derived from the last available candle timestamp. The
CLI prints a warning naming the augmented contracts.

For the longest local CNY/RUB history, build a separate long series from both
`CY*` and `CR*` families. `CNYRUB_CONT_LONG` is intentionally distinct from the
older `CNYRUB_CONT` CR-only output: it crosses a family boundary and its quality
report records that transition.

Build `CNYRUB_CONT_LONG` unadjusted into this repository's `data/derived`:

```bash
uv run continuous-futures build \
  --data-root ../moex_iss_adapter/data \
  --output-root data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --continuous-symbol CNYRUB_CONT_LONG \
  --timeframe 1d \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --secid-prefix CY \
  --secid-prefix CR \
  --roll-rule fixed_days_before_expiration \
  --roll-days-before 3 \
  --adjustment-mode unadjusted \
  --save \
  --overwrite
```

Build the back-adjusted variant separately:

```bash
uv run continuous-futures build \
  --data-root ../moex_iss_adapter/data \
  --output-root data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --continuous-symbol CNYRUB_CONT_LONG \
  --timeframe 1d \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --secid-prefix CY \
  --secid-prefix CR \
  --roll-rule fixed_days_before_expiration \
  --roll-days-before 3 \
  --adjustment-mode back_adjusted \
  --save \
  --overwrite
```

Validate both outputs:

```bash
uv run continuous-futures validate \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet

uv run continuous-futures validate \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

Inspect compact quality reports:

```bash
uv run continuous-futures quality \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet

uv run continuous-futures quality \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=back_adjusted/part-000.parquet
```

The quality report prints row count, date range, contracts used, roll count,
roll dates, gap count, duplicate timestamp count, null contract/price counts,
families/prefixes used, family transition count and diagnostics, roll-gap
summary with the roll date/contracts responsible for the maximum absolute gap,
family-transition gap summary, and adjustment-factor range for back-adjusted
output. It does not print the full DataFrame.

Apply the policy-based quality gate before using generated Parquet in research:

```bash
uv run continuous-futures quality-gate \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --policy configs/quality_gate.default.yaml
```

Check several datasets and write a manifest:

```bash
uv run continuous-futures quality-gate \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=USDRUB_CONT/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --policy configs/quality_gate.default.yaml \
  --save \
  --output data/reports/continuous_futures/quality_gate_manifest.json
```

The gate exits `0` only when every dataset is `approved`; `rejected` and
`split_required` both exit `1`. See
`docs/architecture/data_quality_gate.md`.

Detailed roll diagnostics:

```bash
uv run continuous-futures roll-report \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet
```

Top roll gaps:

```bash
uv run continuous-futures roll-report \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --top 10
```

Family transitions only:

```bash
uv run continuous-futures roll-report \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --family-transitions-only
```

Save diagnostics for manual and programmatic review:

```bash
uv run continuous-futures roll-report \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --save \
  --output-dir data/reports/continuous_futures/CNYRUB_CONT_LONG/unadjusted
```

This writes `roll_report.parquet`, `roll_report.csv`, and
`quality_summary.json`. Reports under `data/` are generated artifacts and should
not be committed.

Investigate a single roll outlier with source candles and nearby candidate
contracts:

```bash
uv run continuous-futures investigate-roll \
  --data-root ../moex_iss_adapter/data \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --roll-date 2020-02-06 \
  --from-contract CYH0 \
  --to-contract CYM2 \
  --metadata-snapshot-ts <SNAPSHOT_TS>
```

Use `--save --output-dir <DIR>` to write `investigation_summary.json`,
`continuous_window.csv`, `from_contract_window.csv`, `to_contract_window.csv`,
and `candidate_contracts.csv`. The structural-gap flags in this report are
diagnostic only and do not alter the built Parquet. See
`docs/architecture/roll_outlier_investigation.md`.

## USD/RUB Control Series

`USDRUB_CONT` is a control continuous series built from concrete `Si*` futures
already downloaded by `moex_iss_adapter`. It checks whether the same builder
pipeline can produce a dense, long history for a historically liquid family.
The control does not add USD/RUB-specific code; it uses the same generic
metadata snapshot, query, SECID-prefix, roll rule, and adjustment-mode options.

```bash
uv run continuous-futures build \
  --data-root ../moex_iss_adapter/data \
  --output-root data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --continuous-symbol USDRUB_CONT \
  --timeframe 1d \
  --metadata-snapshot-ts 20260705T093425Z \
  --query Si \
  --secid-prefix Si \
  --roll-rule fixed_days_before_expiration \
  --roll-days-before 3 \
  --adjustment-mode unadjusted \
  --save \
  --overwrite
```

Build the back-adjusted variant by changing `--adjustment-mode` to
`back_adjusted`.

The `Si*` control built from the 20260705T093425Z adapter snapshot produced a
valid 2015-01-05 to 2026-07-03 series with no family transition and no CNY-like
2020-2022 structural source gap. In the same local dataset,
`CNYRUB_CONT_LONG` has a 734-day source coverage gap at `CYH0 -> CYM2`, while
the largest `USDRUB_CONT` roll source coverage gap is only 5 calendar days.
This supports treating the CNY/RUB issue as instrument/data-family specific
rather than a broad adapter or builder pipeline failure.

Family transitions are generic. The builder compares inferred SECID prefixes
or the explicit user-provided prefixes and reports transitions such as
`CYM2 -> CRM2` without treating `CY` or `CR` specially. A large transition gap
is surfaced as a diagnostic warning, but the transition is not automatically
forbidden.

Large roll gaps are not automatically errors. They may reflect a real family
transition, sparse historical data, different liquidity, or a roll date where
the next contract's available close is far from the previous active close. They
must be inspected with `roll-report --top`.

For `back_adjusted`, a large cumulative `adjustment_factor` can be expected when
many additive roll gaps are accumulated backward through history. It should be
reviewed against the roll report, especially around family transitions.

Sparse-contract pruning is documented in
`docs/architecture/sparse_contract_policy.md`. Build output includes
`contracts_in_chain`, `contracts_used`, `contracts_pruned`, `pruned_contracts`,
`pruning_reasons`, and `empty_active_segments`. The pruning policy is currently
not configurable; this keeps output semantics stable until a separate threshold
policy is accepted.

Automated tests use fixtures only. They do not require `../moex_iss_adapter/data` and do not call MOEX ISS.

## Roll Rule

`fixed_days_before_expiration` rolls each contract to the next eligible contract on the Nth available trading day before the current contract expiration. Trading days are taken from the current contract candle data. If fewer than N prior trading days exist near expiration, the earliest available prior trading day is used and a warning is recorded. If the current contract expiration is still beyond the available candle history, the builder keeps the current contract active and does not roll into future contracts early. If the next contract has no candle data on or after a real roll date, building fails.

## Adjustment Modes

`unadjusted` switches contracts at roll dates and preserves raw tradable prices.

`back_adjusted` uses deterministic additive back-adjustment:

```text
gap = new_contract_close_on_roll_date - old_contract_close_on_previous_trading_day
```

Historical rows before each roll receive the cumulative additive offset. The offset is recorded in `adjustment_factor` and applied only to OHLC prices. `volume` and `value` remain unadjusted.

## Tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit tests/contract
uv run pytest tests/integration
```

All tests use tiny local deterministic fixtures under `tests/fixtures/data`.

## Known Limitations

- MVP supports only the fixed-days-before-expiration roll rule.
- MVP supports additive back-adjustment only.
- Volume/open-interest-based rolls are intentionally not implemented.
- The data-store integration uses the current public `ohlcv_data_store.DataStore` API. If that API changes, only `data_access.py` should need updates.
