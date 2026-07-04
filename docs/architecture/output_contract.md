# Output Contract

Derived continuous futures output is written under:

```text
derived/continuous_futures/
  exchange=MOEX/
    engine=futures/
      market=forts/
        continuous_symbol=CNYRUB_CONT/
          timeframe=1d/
            adjustment_mode=back_adjusted/
              part-000.parquet
```

The builder never writes derived output into `normalized/candles`.

Required columns:

```text
source
exchange
engine
market
continuous_symbol
timeframe
ts
open
high
low
close
volume
value
currency
active_contract
active_board
source_contract_symbol
roll_id
roll_flag
roll_date
adjustment_mode
adjustment_factor
```

Validation checks:

- all required columns exist
- `active_contract` is non-null
- `source_contract_symbol` is non-null
- `adjustment_mode` is `unadjusted` or `back_adjusted`
- `adjustment_factor` is numeric
- `ts` is sorted ascending
- duplicate `ts` rows are not allowed

Traceability is mandatory. Every output row identifies the concrete contract that supplied the price, which lets future research use the synthetic series while preserving the executable contract identity.
