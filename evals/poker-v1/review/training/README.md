# PokerBench preflop training review sample

This directory contains one converted example for human review. It is not a frozen suite and is not a complete training set. No deduplication, group split, train/calibration/development/test partitioning, training, or model evaluation was performed. Do not load this sample as a benchmark or treat it as the finalized train split.

The source is the pinned RZ412/PokerBench revision `7ac61f961c81a50fc0f667820b2fb0e432dfec0d`, file `preflop_60k_train_set_prompt_and_label.json`, top-level JSON row index 2 (zero-based), split `train`. The full local file SHA-256 is `dded3b40abf43a2db17f5c4ea721a7fffe272eb59b39f6b018fa101ffe391195` (59245299 bytes). See `source.json` for the exact original object and URL.

Conversion uses the pure `convert` helper in `scripts/build_poker_v1.py`; the output state is byte-for-byte the source instruction. The metadata identifies this as `pokerbench_preflop_train`, keeps the original output, and carries the train file provenance. This single review item is marked `review_exposed`; it is not a partition or training run.
