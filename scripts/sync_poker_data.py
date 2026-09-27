"""Pin and sync the allowlisted Poker v1 data files to iknil/poker-v1."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kev.suite import digest, read_json

REPO_ID = "iknil/poker-v1"
REPO_TYPE = "dataset"
DEFAULT_LOCAL_DIR = ROOT / "evals/poker-v1"
DEFAULT_CONFIG = ROOT / "evals/poker-v1-hub.json"
REVIEW_EXTENSIONS = {".json", ".jsonl", ".md", ".gitkeep"}
MANIFEST_EXTENSIONS = {".jsonl", ".json", ".md", ".gitkeep"}
MAX_REVIEW_BYTES = 5 * 1024 * 1024


def _relative_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or not name or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe Hub path in manifest: {name!r}")
    return path


def _safe_file(root: Path, name: str, *, require_file: bool = True) -> Path:
    """Resolve an allowlisted path without following a child symlink or escaping root."""
    root = root.resolve()
    path = root
    for part in _relative_path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"refusing symlink in data path: {name}")
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"data path escapes suite directory: {name}")
    if require_file and not path.is_file():
        raise ValueError(f"allowlisted file is missing or not regular: {name}")
    return path


def validate_hashes(expected: object) -> dict[str, str]:
    if not isinstance(expected, dict) or not expected:
        raise ValueError("sync config contains no pinned file hashes")
    result = {}
    for name, value in expected.items():
        relative = _relative_path(name)
        if relative.as_posix() == "manifest.json":
            pass
        elif relative.parts[0] == "review":
            if relative.name != ".gitkeep" and relative.suffix.casefold() not in REVIEW_EXTENSIONS:
                raise ValueError(f"sync config includes a non-review artifact: {name}")
        elif relative.suffix.casefold() not in MANIFEST_EXTENSIONS and relative.name != ".gitkeep":
            raise ValueError(f"sync config includes a non-data artifact: {name}")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"sync config has an invalid SHA256 for {name}")
        result[relative.as_posix()] = value
    if "manifest.json" not in result:
        raise ValueError("sync config does not pin manifest.json")
    return result


def collect_files(local_dir: Path) -> list[str]:
    """Return manifest-listed files plus small review artifacts, in stable order."""
    local_dir = local_dir.resolve()
    manifest_path = local_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"missing regular suite manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    entries = manifest.get("files")
    if not isinstance(entries, dict):
        raise ValueError("suite manifest must contain a files mapping")

    names = {"manifest.json"}
    for name, info in entries.items():
        relative = _relative_path(name)
        if relative.suffix.casefold() not in MANIFEST_EXTENSIONS and relative.name != ".gitkeep":
            raise ValueError(f"manifest contains a non-data file: {name}")
        if not isinstance(info, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256", ""))):
            raise ValueError(f"manifest has no valid SHA256 for {name}")
        names.add(relative.as_posix())

    review = local_dir / "review"
    if review.exists():
        if review.is_symlink() or not review.is_dir():
            raise ValueError("review must be a regular directory")
        for path in review.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"review contains a symlink: {path.relative_to(local_dir)}")
            if not path.is_file() or (path.name != ".gitkeep" and path.suffix.casefold() not in REVIEW_EXTENSIONS):
                continue
            if path.stat().st_size > MAX_REVIEW_BYTES:
                raise ValueError(f"review artifact is too large: {path.relative_to(local_dir)}")
            names.add(path.relative_to(local_dir).as_posix())

    for name in sorted(names):
        _safe_file(local_dir, name)
    expected_hashes = {name: info["sha256"] for name, info in entries.items()}
    for name, expected in expected_hashes.items():
        path = _safe_file(local_dir, name)
        if digest(path) != expected:
            raise ValueError(f"suite file differs from its manifest: {name}")
        if path.stat().st_size != entries[name].get("bytes"):
            raise ValueError(f"suite file byte count differs from its manifest: {name}")
        records = entries[name].get("records")
        if records is not None and sum(1 for line in path.open(encoding="utf-8") if line.strip()) != records:
            raise ValueError(f"suite file count differs from its manifest: {name}")
    return sorted(names)


def file_hashes(local_dir: Path, names: list[str] | None = None) -> dict[str, str]:
    names = names if names is not None else collect_files(local_dir)
    return {name: digest(local_dir / Path(*_relative_path(name).parts)) for name in names}


def verify_hashes(local_dir: Path, expected: dict[str, str]) -> None:
    expected = validate_hashes(expected)
    manifest_entries = read_json(_safe_file(local_dir, "manifest.json")).get("files", {})
    required_names = {"manifest.json", *manifest_entries}
    missing_manifest_entries = sorted(required_names - set(expected))
    if missing_manifest_entries:
        raise ValueError(f"sync config omits manifest files: {', '.join(missing_manifest_entries[:5])}")
    actual_names = set(collect_files(local_dir))
    expected_names = set(expected)
    missing = sorted(expected_names - actual_names)
    if missing:
        raise ValueError(f"local suite is missing pinned files: {', '.join(missing[:5])}")
    for name, wanted in expected.items():
        got = digest(_safe_file(local_dir, name))
        if got != wanted:
            raise ValueError(f"SHA256 mismatch for {name}: expected {wanted}, got {got}")


def load_config(path: Path) -> dict:
    config = read_json(path)
    if config.get("repo_id") != REPO_ID or config.get("repo_type") != REPO_TYPE:
        raise ValueError(f"sync config must pin {REPO_TYPE} repo {REPO_ID}")
    if config.get("schema_version") != 1:
        raise ValueError("unsupported sync config schema")
    if config.get("path_in_repo"):
        _relative_path(config["path_in_repo"])
    return config


def parse_commit_revision(output: str, stderr: str = "") -> str:
    """Read a commit SHA from hf's JSON or text upload result."""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = None
    candidates: list[str] = []

    def visit(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key).casefold())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and "commit" in key:
            candidates.extend(re.findall(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", value.casefold()))

    if payload is not None:
        visit(payload)
    candidates.extend(re.findall(r"/(?:commit|tree)/([0-9a-f]{40})(?![0-9a-f])", output.casefold()))
    candidates.extend(re.findall(r"/(?:commit|tree)/([0-9a-f]{40})(?![0-9a-f])", stderr.casefold()))
    unique = sorted(set(candidates))
    if len(unique) != 1:
        raise ValueError("could not identify one commit SHA in `hf upload --format json` output")
    return unique[0]


def _atomic_write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _run_hf(command: list[str]) -> subprocess.CompletedProcess[str]:
    for attempt in range(2):
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            detail = f"{error.stdout or ''}\n{error.stderr or ''}".strip()
            transient = any(term in detail.casefold() for term in ("ssl", "connection reset", "remote disconnected"))
            if attempt == 0 and transient:
                continue
            raise RuntimeError(f"Hub CLI failed ({error.returncode}): {detail or error.cmd}") from error
    raise AssertionError("unreachable")


def upload(local_dir: Path, config_path: Path) -> str:
    names = collect_files(local_dir)
    local_dir, config_path = local_dir.resolve(), config_path.resolve()
    if local_dir == config_path.parent or local_dir in config_path.parents:
        raise ValueError("sync config must live outside the suite directory")
    with tempfile.TemporaryDirectory(prefix="poker-v1-hub-upload-") as temporary:
        staging = Path(temporary)
        for name in names:
            target = staging / Path(*_relative_path(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_dir / Path(*_relative_path(name).parts), target)
        collect_files(staging)
        hashes = file_hashes(staging, names)
        result = _run_hf(
            ["hf", "upload", REPO_ID, str(staging), ".", "--type", REPO_TYPE,
             "--commit-message", "Sync Poker v1 data suite", "--format", "json"]
        )
    revision = parse_commit_revision(result.stdout, result.stderr)
    config = {"schema_version": 1, "repo_id": REPO_ID, "repo_type": REPO_TYPE,
              "revision": revision, "files": hashes}
    _atomic_write_config(config_path, config)
    return revision


def _preflight_publish(source: Path, target: Path, hashes: dict[str, str]) -> None:
    hashes = validate_hashes(hashes)
    source, target = source.resolve(), target.resolve()
    manifest_entries = read_json(_safe_file(source, "manifest.json")).get("files", {})
    missing_manifest_entries = sorted({"manifest.json", *manifest_entries} - set(hashes))
    if missing_manifest_entries:
        raise ValueError(f"pinned download omits manifest files: {', '.join(missing_manifest_entries[:5])}")
    for name, wanted in hashes.items():
        src = _safe_file(source, name)
        dst = _safe_file(target, name, require_file=False)
        if digest(src) != wanted:
            raise ValueError(f"downloaded file failed verification: {name}")
        if dst.exists() or dst.is_symlink():
            if not dst.is_file() or digest(dst) != wanted:
                raise FileExistsError(f"refusing to overwrite different local file: {dst}")


def download(local_dir: Path, config_path: Path) -> None:
    config = load_config(config_path)
    revision = config.get("revision")
    hashes = config.get("files", {})
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("sync config has no pinned 40-character Hub commit")
    hashes = validate_hashes(hashes)
    prefix = config.get("path_in_repo", "")

    local_dir = local_dir.resolve()
    config_path = config_path.resolve()
    if local_dir in config_path.parents:
        raise ValueError("sync config must live outside the download directory")
    local_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="poker-v1-hub-download-") as temporary:
        staging = Path(temporary)
        remote_names = [f"{prefix}/{name}" if prefix else name for name in sorted(hashes)]
        command = ["hf", "download", REPO_ID, *remote_names, "--type", REPO_TYPE,
                   "--revision", revision, "--local-dir", str(staging)]
        _run_hf(command)
        downloaded_root = staging / prefix if prefix else staging
        _preflight_publish(downloaded_root, local_dir, hashes)
        for name in sorted(hashes):
            src = _safe_file(downloaded_root, name)
            dst = _safe_file(local_dir, name, require_file=False)
            if dst.exists() or dst.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            _safe_file(local_dir, name, require_file=False)
            temporary_file = None
            try:
                with tempfile.NamedTemporaryFile("wb", dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp", delete=False) as stream:
                    temporary_file = Path(stream.name)
                    with src.open("rb") as source:
                        shutil.copyfileobj(source, stream)
                os.replace(temporary_file, dst)
            finally:
                if temporary_file is not None:
                    temporary_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync only the pinned Poker v1 data suite with its Hub dataset.")
    parser.add_argument("operation", choices=("upload", "download", "verify"))
    parser.add_argument("--local-dir", type=Path, default=DEFAULT_LOCAL_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    local_dir, config_path = args.local_dir.resolve(), args.config.resolve()

    if args.operation == "upload":
        revision = upload(local_dir, config_path)
        print(f"uploaded {REPO_ID}@{revision}; pinned {len(load_config(config_path)['files'])} files in {config_path}")
    elif args.operation == "download":
        download(local_dir, config_path)
        print(f"downloaded and verified {REPO_ID}@{load_config(config_path)['revision']} into {local_dir}")
    else:
        config = load_config(config_path)
        revision = config.get("revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("sync config has no pinned 40-character Hub commit")
        verify_hashes(local_dir, config.get("files", {}))
        print(f"verified {len(config['files'])} files against {REPO_ID}@{revision}")


if __name__ == "__main__":
    main()
