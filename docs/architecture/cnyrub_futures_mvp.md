# CNY/RUB Futures MVP

The first practical target is a MOEX CNY/RUB-like futures family:

```text
exchange = MOEX
engine = futures
market = forts
timeframe = 1d
continuous_symbol = CNYRUB_CONT
```

The MVP reads a metadata snapshot and local candle Parquet files. Contract discovery is conservative and configurable:

- `query` searches `secid`, `shortname`, `secname`, and `assetcode`.
- `asset_code` filters by exact asset code.
- `secid_prefix` filters by SECID prefix.
- `include_secid` forces known contracts into the candidate set.
- `exclude_secid` removes contracts.

The builder sorts eligible contracts by effective expiration date. `expiration_date` is preferred; `last_trade_date` is used only when `expiration_date` is missing. If both are missing, the contract is skipped with an explicit warning.

The fixture chain uses fake but realistic contracts:

```text
CRH4 -> CRM4 -> CRU4 -> CRZ4
```

Each contract keeps its board, so the output can preserve both `active_contract` and `active_board`.
