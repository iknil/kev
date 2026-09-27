# poker-v1

This eval-only suite converts the official PokerBench preflop and postflop test prompts into Kev Choice requests. Each `state` is the exact upstream `instruction` string. It does not use either structured CSV, so source rows are never joined by line number.

The pinned sources are `RZ412/PokerBench` at revision `7ac61f961c81a50fc0f667820b2fb0e432dfec0d`: 1,000 preflop and 10,000 postflop prompts. Their SHA256 values are in `manifest.json`. All 11,000 source rows appear in `test.jsonl`; `quarantine.jsonl` is empty. `train.jsonl`, `calibration.jsonl`, and `development.jsonl` are empty. Do not use this suite for training or model selection.

The action state is reconstructed from the instruction history and 100-chip starting stacks, with 0.5/1-chip blinds. The source's stated pot controls the playground bet-size formulas. The replayed contribution total often differs from that pot, so the difference is recorded in each record's `_meta.replay`; it does not cause quarantine. Preflop positions after the actor remain active until they have a turn. Unmentioned earlier preflop positions and unmentioned postflop positions are folded.

The candidate schedule follows `playground/src/lib/poker/candidates.ts`, including its positive-number JavaScript rounding and pot fractions. It keeps exact fractional minimum and maximum bounds so the all-in option remains available for half-chip stacks. Fold is always included, including when check is available. Wager amounts are totals paid on the current street, and raises say `Raise to ...`. The source gold amount replaces the nearest same-action interior amount, with ties going to the lower amount. Minimum and all-in candidates stay in place. If no interior amount can be replaced, the gold amount is added and the exception is recorded.

The user authorized correcting 73 PokerBench integer labels that truncate a fractional all-in by exactly 0.5 or 0.7 chips. The builder applies this only when aggression is legal, `min_to == max_to`, the gold is an integer equal to `floor(max_to)`, and the exact difference is 0.5 or 0.7. It offers and labels the precise all-in amount while preserving the original instruction and `original_output`; `_meta.candidate_policy.gold_correction` records the reason, original amount, and corrected amount. The report lists all corrected source rows and differences. Other illegal gold amounts remain quarantined.

PokerBench's 98 gold `raise` labels in unopened postflop states are normalized to `bet` in the label while `_meta.original_output` keeps the source text and `_meta.candidate_policy.normalization_reason` explains the change. Historical raises below the minimum that are not all-in, and history verbs that differ from the current wager state, remain unchanged in `state` and are recorded under `_meta.replay.source_history_warnings`.

Rows 18, 20, and 34 in the postflop source were exposed during review. `test/sample.jsonl` mirrors row 18 and is not an additional evaluation row. Row 18 offers fold, check, and bets to 1, 5, 12, 16, 24, and 92 chips. Row 34 offers the source's raise-to-88 all-in.

Because the source gold amount determines one offered candidate, this eval measures action selection when the gold amount is available in the options. It does not measure how the playground chooses a candidate schedule without knowing the answer. No model was run or trained for this migration.

`manifest.json`, `validation-report.json`, and `validation.json` record source hashes, partition hashes and counts, conversion counts, corrections, replay warnings, compatibility checks, and the local tokenizer context check. The context is the serving limit (`truncate: false`), not Kev's 384-token training admission rule.

Build from the pinned files already downloaded to `/tmp`:

```bash
PYTHONPATH=. .venv/bin/python scripts/build_poker_v1.py --source-dir /tmp
```

Score the locked test only when evaluation is authorized:

```bash
uv run python -m kev.benchmark --run <checkpoint> --suite evals/poker-v1 --allow-test --out runs/poker-v1
```
