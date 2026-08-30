# Continuous Futures Data Quality Gate

The quality gate turns diagnostic signals into a reproducible approve/block
decision for generated continuous futures Parquet files. It is intentionally
generic: it does not hardcode `CY`, `CR`, `Si`, CNY/RUB, USD/RUB, or any other
instrument family.

## Statuses

`approved` means the file passes schema validation and the configured policy
thresholds. These datasets are marked `approved_for_research: true`.

`rejected` means the file is structurally unsafe as a research input. The
default policy rejects duplicate timestamps, null OHLC prices, null active
contracts, and schema validation failures.

`split_required` means the file is technically readable, but the roll path
contains a structural research break. The default policy marks a split when a
roll timestamp/source coverage gap or roll price gap exceeds the threshold.

The CLI exits `0` only when every checked dataset is `approved`. It exits `1`
for `rejected` or `split_required`.

## Default Policy

The default policy lives in `configs/quality_gate.default.yaml`:

```yaml
max_duplicate_ts: 0
max_null_price_count: 0
max_null_active_contract_count: 0
max_source_coverage_gap_days: 30
max_roll_gap_pct: 25.0
allow_family_transitions: true
max_family_transition_gap_pct: 25.0
allow_empty_active_segments: false
require_validation_status_ok: true
```

Custom policy files are supported in flat YAML, JSON, and TOML.

## Metrics

The gate reports:

- schema validation status and error text
- row count and date range
- duplicate timestamp count
- null OHLC price row count
- null active contract count
- active contract and roll counts
- maximum absolute roll gap and roll gap percent
- maximum roll timestamp/source coverage gap
- family transition count and maximum family transition gap percent
- empty active segment and pruned contract fields when available

Path-only gate runs do not read source candle partitions. Because the generated
continuous Parquet does not encode the complete source coverage for inactive or
pruned contracts, `max_source_coverage_gap_days` is computed from the timestamp
gap between the previous active row and the new active row at each roll. Use
`investigate-roll` when full source candle coverage and pruned candidate
contracts are needed.

## CLI

Check one dataset:

```bash
uv run continuous-futures quality-gate \
  --path data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet
```

Check several datasets and save a manifest:

```bash
uv run continuous-futures quality-gate \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=USDRUB_CONT/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --paths data/derived/continuous_futures/exchange=MOEX/engine=futures/market=forts/continuous_symbol=CNYRUB_CONT_LONG/timeframe=1d/adjustment_mode=unadjusted/part-000.parquet \
  --policy configs/quality_gate.default.yaml \
  --save \
  --output data/reports/continuous_futures/quality_gate_manifest.json
```

The manifest includes each dataset's status, reasons, metrics,
`approved_for_research` flag, and `dataset_sha256`. Consumers must verify the hash so a different
file, or modified content at the same path, cannot inherit an earlier approval. Generated reports
under `data/` should not be committed.

## Expected Local Outcomes

With the current local generated Parquet:

- `USDRUB_CONT` should be `approved`.
- `CNYRUB_CONT` should be `approved`.
- `CNYRUB_CONT_LONG` should be `split_required`.

`CNYRUB_CONT_LONG` is blocked generically by a large roll gap and a long
timestamp/source coverage gap around the historical `CY*` roll. The gate does
not special-case that symbol; any series with the same metrics would get the
same decision.
