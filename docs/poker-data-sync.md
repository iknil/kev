# Poker v1 data sync

`evals/poker-v1-hub.json` pins the full suite at `iknil/poker-v1`. `evals/poker-v1-lite-hub.json` pins the lite suite in the same repository under `lite/`. Both pin the final Hub commit and each file's SHA256. The sync script reads only files listed in the full suite's manifest, that manifest, and small JSON, JSONL, Markdown, or `.gitkeep` files under `review/`. It never stages model weights or unrelated files.

Before uploading, sign in with `hf auth login` using a token that can write to `iknil/poker-v1`. Upload the current suite with:

```sh
uv run python scripts/sync_poker_data.py upload
```

The script verifies the suite manifest hashes and record counts before staging. It reads the commit URL returned by `hf upload` and writes that commit SHA with the uploaded file hashes to `evals/poker-v1-hub.json`. The config is separate from the frozen suite manifest. The lite suite was uploaded to `lite/` in a subsequent commit, so both checked-in configs pin that final commit.

Verify a local suite against the pinned config without network access:

```sh
uv run python scripts/sync_poker_data.py verify
uv run python scripts/sync_poker_data.py verify --local-dir evals/poker-v1-lite --config evals/poker-v1-lite-hub.json
```

Download the pinned revision. The script checks every downloaded hash in a temporary directory first. It refuses to replace any existing different file, then copies missing files into the target.

```sh
uv run python scripts/sync_poker_data.py download
uv run python scripts/sync_poker_data.py download --local-dir /data/poker-v1
uv run python scripts/sync_poker_data.py verify --local-dir /data/poker-v1
uv run python scripts/sync_poker_data.py download --local-dir /data/poker-v1-lite --config evals/poker-v1-lite-hub.json
```

`--config` selects another local copy of the tracked pin file. Download requires that config to contain a 40-character Hub commit and hashes for every manifest-listed file. Extra local files are left untouched.
