# continuous-futures-builder

This project builds derived continuous futures datasets from local normalized data. It does not download market data and does not run backtests.

`continuous-futures-builder` is a standalone research-data builder for stitching concrete futures contracts into traceable continuous futures series. The MVP targets MOEX CNY/RUB-like futures with daily candles, fixed-days-before-expiration rolls, and two output modes: `unadjusted` and `back_adjusted`.

## What It Does

- Reads local normalized futures candles and metadata snapshots.
- Builds a configurable contract chain from metadata.
- Builds a fixed-days-before-expiration roll calendar.
- Stitches concrete contracts into a continuous series.
- Preserves `active_contract` and `source_contract_symbol` on every output row.
- Writes derived Parquet under `data_root/derived/continuous_futures/...`.

## What It Does Not Do

- It does not import `moex_iss_adapter`.
- It does not call MOEX ISS or any market-data API.
- It does not normalize raw exchange responses.
- It does not run backtests, strategies, PnL, execution, visualization, web APIs, or database infrastructure.

## Relationship To Other Projects

`moex_iss_adapter` is responsible for downloading and normalizing concrete contract data. This project only reads already-normalized local data.

`ohlcv-data-store` is the intended stable read layer. The MVP includes a small local normalized-layout reader for deterministic fixtures and file-based use; integration with the public `ohlcv_data_store` API can be added without changing the continuous futures logic.

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

## Output Schema

Required output columns:

```text
source, exchange, engine, market, continuous_symbol, timeframe, ts,
open, high, low, close, volume, value, currency,
active_contract, active_board, source_contract_symbol,
roll_id, roll_flag, roll_date, adjustment_mode, adjustment_factor
```

`active_contract` and `source_contract_symbol` are non-null on every row so future research or backtests can map the continuous row back to an executable contract.

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
)
```

## CLI

```bash
uv run continuous-futures chain \
  --data-root tests/fixtures/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts 20240101T000000Z \
  --query CR
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

## Roll Rule

`fixed_days_before_expiration` rolls each contract to the next eligible contract on the Nth available trading day before the current contract expiration. Trading days are taken from the current contract candle data. If fewer than N prior trading days exist, the earliest available prior trading day is used and a warning is recorded. If the next contract has no candle data on or after the roll date, building fails.

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
- The local reader follows the normalized fixture layout; full `ohlcv_data_store` integration is expected later.
