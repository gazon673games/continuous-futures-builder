# Real CNY/RUB Workflow

Real local validation uses sibling project outputs only. No command in this project should contact MOEX ISS or any external market-data API.

Manual workflow:

1. Use `moex_iss_adapter` to download futures metadata and concrete CNY/RUB-like futures candles.
2. Use `ohlcv-data-store` to validate the local normalized adapter output.
3. Use `continuous-futures-builder` to build derived continuous futures Parquet.

The expected local layout is:

```text
ohlcv-replay/
  moex_iss_adapter/
  data_store/
  continuous_futures_builder/
```

Adapter output is typically read from:

```text
../moex_iss_adapter/data
```

List available futures metadata snapshots:

```bash
uv run continuous-futures snapshots \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts
```

Preview a CNY/RUB-like contract chain:

```bash
uv run continuous-futures chain \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY
```

Build the derived back-adjusted series:

```bash
uv run continuous-futures build \
  --data-root ../moex_iss_adapter/data \
  --exchange MOEX \
  --engine futures \
  --market forts \
  --continuous-symbol CNYRUB_CONT \
  --timeframe 1d \
  --metadata-snapshot-ts <SNAPSHOT_TS> \
  --query CNY \
  --roll-rule fixed_days_before_expiration \
  --roll-days-before 3 \
  --adjustment-mode back_adjusted \
  --save \
  --overwrite
```

At least two concrete contracts are required because a continuous futures series needs at least one roll boundary. If the metadata filter finds only one usable contract, chain building fails.

Missing candles around roll dates can also prevent building. The roll calendar requires current-contract trading days before expiration and next-contract data on or after the roll date. Back-adjustment additionally requires the old contract close before the roll and the new contract close on the roll date.
