# Roll Rules

The MVP supports one roll rule:

```text
fixed_days_before_expiration
```

For each pair of consecutive contracts, the builder rolls from the current contract to the next contract before the current contract expires.

The roll date is chosen from trading days present in the current contract candle data:

1. Find current-contract candle dates strictly before expiration.
2. Pick the Nth available trading day before expiration.
3. If fewer than N prior trading days exist, use the earliest available prior date and record a warning.
4. Require the next contract to have candle data on or after the roll date.

The rule does not use future volume or open interest, so the MVP avoids volume-based look-ahead decisions.

Stitching uses the current contract before the roll date and the next contract from the roll date onward. `roll_flag` is true on the first output row where the new contract becomes active.

Back-adjustment uses additive gaps:

```text
gap = new_contract_close_on_roll_date - old_contract_close_on_previous_trading_day
```

Rows before each roll receive the cumulative future gap as `adjustment_factor`. OHLC prices are adjusted by that factor. `volume` and `value` are left unchanged.
