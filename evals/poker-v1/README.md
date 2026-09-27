# PokerBench v1

This suite combines Kev's existing locked PokerBench test with grouped training data from `RZ412/PokerBench` at revision `7ac61f961c81a50fc0f667820b2fb0e432dfec0d`. The instruction remains the state verbatim. Training labels use the reviewed action replay and candidate policy; bet/raise options include the gold amount, so this measures choice among a gold-inclusive candidate set rather than live candidate generation without answer access.

| Partition | Records | Role |
| --- | ---: | --- |
| train | 466,704 | Trainable |
| calibration | 25,929 | Eval-only |
| development | 25,928 | Eval-only |
| test | 11,000 | Locked evaluation |

Training admission was checked only with Qwen3.5-0.8B-Base at `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68` and strict `training_context` limits (384 state, 1,024 branch, 2,048 packed). Other bases require an explicit pinned tokenizer revision and a new admission pass. The root manifest retains the original serving context for the locked test and lists the training context separately. `eval_only=false` exposes the train partition to training tools, so benchmark overlong-record filtering follows the unified manifest policy.

Run the locked evaluation explicitly with:

```sh
uv run python -m kev.benchmark --run runs/<run> --suite evals/poker-v1 --out runs/poker-v1 --allow-test
```

The source files must be named `preflop_60k_train_set_prompt_and_label.json` and `postflop_500k_train_set_prompt_and_label.json` in one directory. Their pinned hashes, candidate/eval helper hashes, and row dispositions are recorded in the root manifest and `dispositions.jsonl`.

```sh
uv run python scripts/build_poker_train_v1.py \
  --source-dir /path/to/pinned-pokerbench-train-files \
  --eval-suite evals/poker-v1 \
  --out /tmp/poker-v1-rebuilt
```

To assemble an already-built `poker-train-v1` directory with an evaluation suite, add `--assemble-existing --training-suite /path/to/poker-train-v1`. Builders refuse to overwrite an output directory with a manifest. Historical evaluation and training manifests, reports, and README files are under `audit/evaluation/` and `audit/training/`; the exposed training example is under `review/training/`.

Future training must set `--p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0` to preserve the reviewed candidate set. These flags are not changed in Kev's CLI defaults. This migration ran no model evaluation or training.
