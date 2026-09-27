import hashlib
import json
import shutil
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts import sync_poker_data as sync


def _write_suite(root: Path) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "test").mkdir()
    (root / "review").mkdir()
    contents = {
        "train.jsonl": '{"state":"train"}\n',
        "test/sample.jsonl": '{"state":"reviewed test"}\n',
    }
    files = {}
    for name, content in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "bytes": path.stat().st_size, "records": 1}
    (root / "review/example.json").write_text('{"review":true}\n', encoding="utf-8")
    (root / "review/NOT_DATA.pt").write_bytes(b"weights")
    (root / "stray.safetensors").write_bytes(b"weights")
    (root / "manifest.json").write_text(json.dumps({"suite": "poker-v1", "files": files}), encoding="utf-8")
    return files


def test_collect_files_is_manifest_allowlist_plus_small_review(tmp_path):
    _write_suite(tmp_path)
    names = sync.collect_files(tmp_path)
    assert names == ["manifest.json", "review/example.json", "test/sample.jsonl", "train.jsonl"]


def test_collect_files_rejects_manifest_mismatch_and_unsafe_paths(tmp_path):
    _write_suite(tmp_path)
    (tmp_path / "train.jsonl").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its manifest"):
        sync.collect_files(tmp_path)

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["../model.safetensors"] = {"sha256": "0" * 64, "bytes": 0, "records": 0}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe Hub path"):
        sync.collect_files(tmp_path)


def test_commit_parser_accepts_hf_commit_url_and_json(tmp_path):
    revision = "a" * 40
    assert sync.parse_commit_revision(json.dumps({"url": f"https://huggingface.co/datasets/{sync.REPO_ID}/commit/{revision}"})) == revision
    assert sync.parse_commit_revision(f"Uploaded: https://huggingface.co/datasets/{sync.REPO_ID}/tree/{revision}") == revision


def test_download_preflight_refuses_symlinks_and_different_files(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    hashes = _write_suite(source)
    source_hashes = {
        "manifest.json": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest(),
        **{name: info["sha256"] for name, info in hashes.items()},
    }
    target.mkdir()
    (target / "train.jsonl").write_text("local data\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        sync._preflight_publish(source, target, source_hashes)

    (target / "train.jsonl").unlink()
    target.mkdir(exist_ok=True)
    (target / "test").symlink_to(source / "test", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        sync._preflight_publish(source, target, source_hashes)


def test_upload_pins_commit_and_only_staged_allowlist(tmp_path, monkeypatch):
    suite = tmp_path / "suite"
    _write_suite(suite)
    config = tmp_path / "poker-v1-hub.json"
    revision = "b" * 40
    observed = {}

    def fake_run(command):
        staging = Path(command[3])
        observed["files"] = sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file())
        observed["command"] = command
        return CompletedProcess(command, 0, stdout=json.dumps({"url": f"https://huggingface.co/datasets/{sync.REPO_ID}/commit/{revision}"}), stderr="")

    monkeypatch.setattr(sync, "_run_hf", fake_run)
    assert sync.upload(suite, config) == revision
    pinned = json.loads(config.read_text(encoding="utf-8"))
    assert pinned["revision"] == revision
    assert "stray.safetensors" not in observed["files"]
    assert "review/NOT_DATA.pt" not in observed["files"]
    assert set(pinned["files"]) == set(observed["files"])


def test_upload_then_download_restores_pinned_files(tmp_path, monkeypatch):
    suite = tmp_path / "suite"
    _write_suite(suite)
    config = tmp_path / "poker-v1-hub.json"
    remote = tmp_path / "remote"
    revision = "c" * 40

    def fake_run(command):
        if command[1] == "upload":
            staging = Path(command[3])
            shutil.copytree(staging, remote, dirs_exist_ok=True)
            output = json.dumps({"url": f"https://huggingface.co/datasets/{sync.REPO_ID}/commit/{revision}"})
        else:
            target = Path(command[command.index("--local-dir") + 1])
            files = command[3:command.index("--type")]
            for name in files:
                src, dst = remote / name, target / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            output = ""
        return CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(sync, "_run_hf", fake_run)
    sync.upload(suite, config)
    destination = tmp_path / "downloaded"
    sync.download(destination, config)
    pinned = json.loads(config.read_text(encoding="utf-8"))
    sync.verify_hashes(destination, pinned["files"])
    assert not (destination / "stray.safetensors").exists()
    assert (destination / "train.jsonl").read_bytes() == (suite / "train.jsonl").read_bytes()
