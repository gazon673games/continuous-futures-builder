# Data Access Boundary

`continuous-futures-builder` reads input through a small data access abstraction:

```text
continuous_futures_builder.data_access
```

The abstraction has two read methods:

```text
load_metadata_snapshot(exchange, engine, market, snapshot_ts)
load_contract_candles(exchange, engine, market, board, symbol, timeframe)
```

Production use prefers the public `ohlcv_data_store` API:

```python
from ohlcv_data_store import DataStore
```

The wrapper calls only public methods:

- `DataStore.get_metadata_snapshot`
- `DataStore.get_candles`
- `DataStore.list_metadata_snapshots`

The fallback local Parquet reader remains intentionally thin for deterministic fixtures and for environments where `ohlcv_data_store` is not installed. It should not grow into a competing data store. Schema validation, path conventions, and normalized dataset discovery belong in `ohlcv-data-store`.

The builder must not import adapter internals because adapter output is a persisted data contract. `moex_iss_adapter` writes normalized Parquet; `ohlcv-data-store` reads and validates it; `continuous-futures-builder` creates derived research datasets from those local files.

If `ohlcv_data_store` is unavailable and local fallback is disabled, the builder raises a clear `InputDataError` that explains how to install the sibling package.
