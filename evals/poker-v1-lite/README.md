# Poker v1 lite

Train, calibration and development retain the original proportional 5% subsets.
Test contains 1000 balanced records from poker-v1/test.jsonl: equal street quotas,
then equal final-label action quotas within each street, subject to availability.
Preflop has four action types; other streets have five. All-in keeps its original
Bet/Raise label. Position, stack depth and amount are not explicitly balanced.
State, options, labels and raw record bytes are unchanged.

| Partition | Records |
| --- | ---: |
| train | 23335 |
| calibration | 1296 |
| development | 1296 |
| test | 1000 |

Rebuild only test in this existing suite:

```sh
uv run python -m scripts.build_poker_eval --parent evals/poker-v1 --suite evals/poker-v1-lite --count 1000 --seed 42
```

To rebuild the entire suite at a fresh path, first run
`uv run python -m scripts.build_poker_lite --out /tmp/poker-v1-lite-rebuilt --fraction 0.05 --seed 42`,
then run the command above with `--suite /tmp/poker-v1-lite-rebuilt`.

Selection uses lowest SHA256(seed:id) within each stratum and preserves source order.
Source and output hashes are recorded. Repeated builds produce identical files.
The balanced test is not independent of the parent test and need not contain all
previous 550 rows. Earlier 550-row results refer to the old suite hash and are not
directly comparable. Test evaluation still requires --allow-test.

For training use --p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0.
