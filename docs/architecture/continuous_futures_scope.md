# Continuous Futures Scope

`continuous-futures-builder` creates derived research datasets from concrete futures contracts that already exist in local normalized storage.

The adapter boundary is intentional:

- `moex_iss_adapter` downloads and normalizes market data.
- `ohlcv-data-store` is the intended stable read API for normalized local data.
- `continuous-futures-builder` reads concrete contracts and writes derived continuous futures output.

Continuous futures do not belong in the adapter because they are not exchange facts. They depend on research choices: contract family filters, roll rules, adjustment modes, and output traceability. Keeping this logic outside the adapter prevents normalized concrete-contract data from being mixed with synthetic research series.

The MVP includes a local normalized-layout reader so tests and fixture builds are deterministic. It does not import `moex_iss_adapter`, call MOEX ISS, download data, or implement backtesting.

Future backtests should read the derived continuous series for clean research prices, but execution logic must still use `active_contract` to map each row back to the real SECID that produced it.
