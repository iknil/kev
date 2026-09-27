"""Exercise the real trainer, PEFT serialization and AdamW on a tiny local base."""
import signal
import sys
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file

from kev import train
from kev.checkpoint import read_meta
from kev.suite import read_json, write_jsonl
from kev.training_state import resolve_resume, save_snapshot


@pytest.fixture
def tiny_training(tmp_path):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM
    from kev.model import SPECIAL
    base = tmp_path / 'base'
    vocab = {word: i for i, word in enumerate(['[UNK]', '[PAD]', *SPECIAL, 'state', 'pick', 'yes', 'no', 'other', *map(str, range(13))])}
    tokenizer = Tokenizer(WordLevel(vocab, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = Whitespace()
    tok = PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token='[UNK]', pad_token='[PAD]', additional_special_tokens=SPECIAL)
    tok.save_pretrained(base)
    torch.manual_seed(42)
    Qwen2ForCausalLM(Qwen2Config(vocab_size=len(tok), hidden_size=16, intermediate_size=32, num_hidden_layers=1,
                                num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=128)).save_pretrained(base)
    data = tmp_path / 'train.jsonl'
    write_jsonl(data, [{'state': f'state {i}', 'questions': {'q': {'type': 'choice', 'instructions': 'pick',
                       'criteria': {'a': 'yes', 'b': 'no', 'c': 'other'}, 'label': ['a', 'b', 'c'][i % 3]}},
                       '_meta': {'id': str(i), 'source': 'custom'}} for i in range(13)])
    return ['--base', str(base), '--data', str(data), '--epochs', '3', '--batch', '2', '--accum', '3',
            '--lora', '2', '--head_dim', '8', '--device', 'cpu', '--lr', '0.001', '--seed', '7',
            '--p_none', '0', '--p_none_distract', '0', '--p_distract', '0', '--p_none_pair', '0.4',
            '--perm_kl', '0.1', '--perm_frac', '1', '--save_every_steps', '2', '--save_total_limit', '2']


def invoke(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['kev.train', *args])
    train.main()


def assert_nested_equal(a, b):
    if isinstance(a, torch.Tensor):
        assert torch.equal(a, b)
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            assert_nested_equal(a[key], b[key])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for left, right in zip(a, b):
            assert_nested_equal(left, right)
    else:
        assert a == b


@pytest.mark.parametrize('interrupt_at', [0, 2, 7, 9])
def test_resume_is_exact_across_accumulation_and_epoch_boundaries(tmp_path, monkeypatch, tiny_training, interrupt_at):
    continuous, interrupted = tmp_path / 'continuous', tmp_path / 'interrupted'
    original_loss = train.batch_loss
    losses = []
    def tracked(*args, **kwargs):
        result = original_loss(*args, **kwargs)
        losses.append(result[0].item())
        return result
    monkeypatch.setattr(train, 'batch_loss', tracked)
    invoke(monkeypatch, [*tiny_training, '--out', str(continuous)])
    expected_losses = losses[:]
    losses.clear()
    def stopping(*args, **kwargs):
        result = tracked(*args, **kwargs)
        if len(losses) == interrupt_at:
            signal.raise_signal(signal.SIGINT if interrupt_at == 2 else signal.SIGTERM)
        return result
    monkeypatch.setattr(train, 'batch_loss', stopping)
    original_save = train.save_snapshot
    def crash_after_save(*args, **kwargs):
        original_save(*args, **kwargs)
        raise RuntimeError('simulated forced termination')
    if interrupt_at == 0:
        monkeypatch.setattr(train, 'save_snapshot', crash_after_save)
        with pytest.raises(RuntimeError, match='forced termination'):
            invoke(monkeypatch, [*tiny_training, '--out', str(interrupted)])
        monkeypatch.setattr(train, 'save_snapshot', original_save)
    else:
        invoke(monkeypatch, [*tiny_training, '--out', str(interrupted)])
    assert not (interrupted / 'training_metrics.json').exists()
    monkeypatch.setattr(train, 'batch_loss', tracked)
    invoke(monkeypatch, ['--resume', str(interrupted)])
    assert losses == expected_losses
    assert_nested_equal(load_file(continuous / 'adapter_model.safetensors'), load_file(interrupted / 'adapter_model.safetensors'))
    assert_nested_equal(read_meta(continuous).head, read_meta(interrupted).head)
    a = torch.load(resolve_resume(continuous) / 'training_state.pt', weights_only=True)
    b = torch.load(resolve_resume(interrupted) / 'training_state.pt', weights_only=True)
    for key in ('optimizer', 'scheduler', 'order', 'step', 'epoch', 'next_microbatch', 'seen', 'tokens_seen', 'run', 'rng'):
        # Global Python RNG is unused by training; its initial state may differ between independent runs.
        if key == 'rng':
            for rng_key in ('torch', 'shuffle'):
                assert_nested_equal(a[key][rng_key], b[key][rng_key])
        else:
            assert_nested_equal(a[key], b[key])
    assert len(list((interrupted / 'checkpoints').glob('step-*'))) == 2
    if interrupt_at == 0:
        unsaved = tmp_path / 'unsaved'
        invoke(monkeypatch, [*tiny_training, '--save_every_steps', '0', '--out', str(unsaved)])
        assert not (unsaved / 'checkpoints').exists()
        assert_nested_equal(load_file(continuous / 'adapter_model.safetensors'), load_file(unsaved / 'adapter_model.safetensors'))
        assert_nested_equal(read_meta(continuous).head, read_meta(unsaved).head)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, ['--resume', str(interrupted), '--batch', '3'])
    # Same path, different contents must not be admitted on resume.
    data = Path(tiny_training[tiny_training.index('--data') + 1])
    data.write_text(data.read_text(encoding='utf-8').replace('state 0', 'state changed'), encoding='utf-8')
    with pytest.raises(ValueError, match='requests_sha256'):
        invoke(monkeypatch, ['--resume', str(interrupted)])


def test_incomplete_save_keeps_last_checkpoint_and_checks_integrity(tmp_path):
    def model(path):
        (path / 'adapter.txt').write_text('weights', encoding='utf-8')
    save_snapshot(tmp_path, 1, {'step': 1}, {}, model, 2)
    old = resolve_resume(tmp_path)
    def failing(path):
        model(path)
        raise OSError('disk full')
    with pytest.raises(OSError, match='disk full'):
        save_snapshot(tmp_path, 2, {'step': 2}, {}, failing, 2)
    assert resolve_resume(tmp_path) == old
    assert not list((tmp_path / 'checkpoints').glob('.saving-*'))
    (old / 'adapter.txt').write_text('corrupted', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        resolve_resume(tmp_path)


def test_publication_failure_keeps_latest_and_orphan_can_be_retried(tmp_path, monkeypatch):
    from kev import training_state
    def model(path):
        (path / 'weights.txt').write_text('weights', encoding='utf-8')
    save_snapshot(tmp_path, 1, {}, {}, model, 1)
    original = training_state.os.replace
    def fail_latest(source, destination):
        if Path(destination).name == 'latest.json':
            raise OSError('publication failed')
        original(source, destination)
    monkeypatch.setattr(training_state.os, 'replace', fail_latest)
    with pytest.raises(OSError, match='publication failed'):
        save_snapshot(tmp_path, 2, {}, {}, model, 1)
    assert resolve_resume(tmp_path).name == 'step-000000001'
    monkeypatch.setattr(training_state.os, 'replace', original)
    save_snapshot(tmp_path, 2, {}, {}, model, 1)
    assert resolve_resume(tmp_path).name == 'step-000000002'
    assert len(list((tmp_path / 'checkpoints').glob('step-*'))) == 1
