# Sparse Contract Pruning Policy

Continuous futures are built from concrete contracts. A sparse contract is a
contract whose candles are too incomplete to support a reliable active segment
in the stitched series. Sparse contracts are dangerous because a single thin or
misplaced segment can create artificial jumps, duplicate roll dates, or a
continuous row that appears tradable but is not supported by real candles.

The builder does not insert synthetic prices. It only stitches rows that exist
in normalized input candles. Missing prices must stay visible as a data-quality
problem or as an explicit pruning decision.

## Current Policy

The policy is deterministic and not currently configurable:

- Contracts with no candle before their expiration date are skipped from the
  rollable chain.
- If multiple contracts share the same expiration date, the contract with the
  richer candle summary is kept and the others are pruned as duplicate
  expiration candidates.
- After the roll calendar is built, any contract whose active segment would be
  empty is pruned and the roll calendar is rebuilt without it.
- If the current contract expiration is beyond available candle history, the
  builder keeps that current contract active and does not roll into future
  contracts early.

An empty active segment means the contract exists in the selected chain but has
zero candle rows between its segment start and segment end. That usually means
the roll date is not compatible with available data for that contract.

## Diagnostics

Build output exposes pruning explicitly:

- `contracts_in_chain`
- `contracts_used`
- `contracts_pruned`
- `pruned_contracts`
- `pruning_reasons`
- `empty_active_segments`

Warnings also name skipped contracts and reasons. These diagnostics are part of
normal quality review, not hidden repair.

## Data Error Or Expected Pruning

Pruning is acceptable when a contract is a duplicate expiration candidate, a
known sparse contract, or a future contract that should not be activated before
its roll window exists in the input data.

It is an input-data problem when the expected active contract is missing candles
around a real roll date, when all candidates for an expiration are sparse, or
when a family transition cannot be explained by the requested prefix policy.

The builder intentionally avoids a configurable pruning threshold for now. A
`--min-active-segment-rows` option would change research output semantics and
needs a separate acceptance pass. The current default remains backward
compatible: skip only contracts that cannot produce a non-empty active segment.
