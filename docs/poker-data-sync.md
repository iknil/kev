# Poker v1 data sync

`evals/poker-v1-hub.json` pins the `iknil/poker-v1` dataset revision and SHA256 values after a successful upload. The sync script reads only files listed in `evals/poker-v1/manifest.json`, that manifest, and small JSON, JSONL, Markdown, or `.gitkeep` files under `review/`. It never stages model weights or unrelated files.

Before uploading, sign in with `hf auth login` using a token that can write to `iknil/poker-v1`. Upload the current suite with:

```sh
uv run python scripts/sync_poker_data.py upload
```

The script verifies the suite manifest hashes and record counts before staging. It reads the commit URL returned by `hf upload` and writes that commit SHA with the uploaded file hashes to `evals/poker-v1-hub.json`. The config is separate from the frozen suite manifest. Commit this config file to Git so other machines can download the same Hub revision. Upload approval is pending, so the checked-in config currently has a null revision and the dataset is not yet pinned as synced.

Verify a local suite against the pinned config without network access:

```sh
uv run python scripts/sync_poker_data.py verify
```

Download the pinned revision. The script checks every downloaded hash in a temporary directory first. It refuses to replace any existing different file, then copies missing files into the target.

```sh
uv run python scripts/sync_poker_data.py download
uv run python scripts/sync_poker_data.py download --local-dir /data/poker-v1
uv run python scripts/sync_poker_data.py verify --local-dir /data/poker-v1
```

`--config` selects another local copy of the tracked pin file. Download requires that config to contain a 40-character Hub commit and hashes for every manifest-listed file. Extra local files are left untouched.
