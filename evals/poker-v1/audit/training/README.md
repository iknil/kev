# PokerBench train v1

This suite adapts the pinned `RZ412/PokerBench` training files at revision `7ac61f961c81a50fc0f667820b2fb0e432dfec0d` into Kev choice records. The source instruction is retained byte-for-byte as `state`. Bet/raise candidates include the gold amount, so this data measures choice among a gold-inclusive candidate set and does not establish how a live candidate generator performs without answer access.

| Partition | Records | Role |
| --- | ---: | --- |
| train | 466,704 | Trainable |
| calibration | 25,929 | Eval-only |
| development | 25,928 | Eval-only |
| test | 0 | Empty; external locked test is `evals/poker-v1` |

The 0.8B Qwen3.5 tokenizer at revision `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68` was used for strict, no-truncation admission under `kev.model.training_context()` (384 state, 1,024 branch, 2,048 packed tokens). This is the only tokenizer/base admission checked; any other base or tokenizer needs its own pinned revision and a new admission pass. The preflop and postflop source SHA256 values, candidate helper hash, eval helper hash, partition hashes and counts are in `manifest.json`.

To rebuild, place both pinned source objects in one directory with their original filenames:

```sh
mkdir -p /tmp/pokerbench-train-sources
# Put preflop_60k_train_set_prompt_and_label.json and postflop_500k_train_set_prompt_and_label.json here.
uv run python scripts/build_poker_train_v1.py \
  --source-dir /tmp/pokerbench-train-sources \
  --out evals/poker-train-v1
```

The builder verifies both pinned source hashes and refuses to overwrite an output directory that already has a manifest. It also requires the pinned tokenizer to be available locally. `dispositions.jsonl` records one outcome for each of the 563,200 source rows. `validation-report.json` includes exclusions and correction details.

The `review/` directory contains one previously exposed example; it is part of the train group assignment and is not an additional partition record. Calibration and development use role-specific eval-only sources. States overlapping the locked test set, duplicate states, conflicting labels, unsupported templates, invalid golds, or context overflow are handled by the recorded disposition policy.

Any future training run must set `--p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0` to preserve the reviewed candidate set; this manifest does not change Kev's CLI defaults. No model evaluation or training was run to build this suite.
