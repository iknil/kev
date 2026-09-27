"""Atomic local training snapshots, progress and random-number state."""
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import tempfile

import torch

from .suite import digest, read_json, write_json


def requests_digest(requests):
    sha = hashlib.sha256()
    for request in requests:
        sha.update(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        sha.update(b'\n')
    return sha.hexdigest()


def random_state(rng, device):
    state = {'shuffle': rng.getstate(), 'python': random.getstate(), 'torch': torch.get_rng_state()}
    if device == 'cuda':
        state['cuda'] = torch.cuda.get_rng_state_all()
    if device == 'mps':
        state['mps'] = torch.mps.get_rng_state()
    return state


def restore_random_state(state, rng, device):
    rng.setstate(state['shuffle'])
    random.setstate(state['python'])
    torch.set_rng_state(state['torch'])
    if device == 'cuda':
        if len(state['cuda']) != torch.cuda.device_count():
            raise ValueError('resume requires the same number of visible CUDA devices')
        torch.cuda.set_rng_state_all(state['cuda'])
    if device == 'mps':
        torch.mps.set_rng_state(state['mps'])


def resolve_resume(run):
    run = Path(run)
    marker = read_json(run / 'checkpoints' / 'latest.json')
    name = marker['checkpoint']
    if Path(name).name != name or not name.startswith('step-'):
        raise ValueError('invalid latest checkpoint path')
    path = run / 'checkpoints' / name
    inventory = read_json(path / 'snapshot.json')
    if inventory.get('version') != 1:
        raise ValueError('unsupported training snapshot version')
    for name, sha in inventory['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or digest(path / name) != sha:
            raise ValueError(f'training checkpoint checksum mismatch: {name}')
    return path


def save_snapshot(run, step, state, config, save_model, keep):
    """Publish latest only after all files are durable; prune only after publication."""
    root = Path(run) / 'checkpoints'
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f'step-{step:09d}'
    if destination.exists():
        # A crash after the directory rename but before latest publication can leave
        # this complete orphan. Never replace a checkpoint referenced by latest.
        latest = read_json(root / 'latest.json') if (root / 'latest.json').exists() else {}
        if latest.get('checkpoint') == destination.name:
            raise ValueError(f'checkpoint already published: {destination}')
        shutil.rmtree(destination)
    staging = Path(tempfile.mkdtemp(prefix='.saving-', dir=root))
    try:
        save_model(staging)
        torch.save(state, staging / 'training_state.pt')
        write_json(staging / 'training_config.json', config)
        files = {str(p.relative_to(staging)): digest(p) for p in sorted(staging.rglob('*')) if p.is_file()}
        write_json(staging / 'snapshot.json', {'version': 1, 'files': files})
        for path in staging.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
        fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(staging, destination)
        marker = root / '.latest.tmp'
        write_json(marker, {'checkpoint': destination.name})
        with marker.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(marker, root / 'latest.json')
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        checkpoints = sorted(p for p in root.glob('step-*') if p.is_dir())
        for path in checkpoints[:-keep]:
            shutil.rmtree(path)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


class StopRequest:
    """Defer SIGINT/SIGTERM until the next optimizer boundary."""
    def __init__(self):
        self.requested = False
        self.previous = {}

    def __enter__(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.previous[sig] = signal.signal(sig, self._request)
        return self

    def _request(self, signum, frame):
        self.requested = True
        print('Stop requested; saving after the current optimizer update.', flush=True)

    def __exit__(self, *exc):
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)
