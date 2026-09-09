import importlib.util
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location('request_proof', Path(__file__).parents[1]/'scripts/fuse-request-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def elf_fixture():
    data = bytearray(768)
    names = b'\0fuse_req_ctx\0fuse_fs_open\0'
    struct.pack_into('<16sHHIQQQIHHHHHH', data, 0, b'\x7fELF\x02\x01'+b'\0'*10,
                     3, 62, 1, 0, 64, 512, 0, 64, 56, 1, 64, 4, 0)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 128, 0x2000, 0, 16, 16, 4096)
    data[256:256+len(names)] = names
    struct.pack_into('<IBBHQQ', data, 408, 1, 0x12, 0, 3, 0x2004, 4)
    struct.pack_into('<IBBHQQ', data, 432, names.index(b'fuse_fs_open'), 0x12, 0, 3, 0x2008, 4)
    struct.pack_into('<IIQQQQIIQQ', data, 576, 0, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
    struct.pack_into('<IIQQQQIIQQ', data, 640, 0, 11, 0, 0, 384, 72, 1, 0, 8, 24)
    struct.pack_into('<IIQQQQIIQQ', data, 704, 0, 1, 6, 0x2000, 128, 16, 0, 0, 16, 0)
    return data


def test_elf_symbol_virtual_addresses_become_file_offsets():
    assert proof.elf_symbols(elf_fixture()) == {'fuse_req_ctx': 132, 'fuse_fs_open': 136}


@pytest.mark.parametrize('data', [b'not ELF', elf_fixture()[:600], b'\x7fELF\x01\x01'+b'\0'*800])
def test_unsupported_or_truncated_elf_is_rejected(data):
    with pytest.raises(ValueError):
        proof.elf_symbols(data)


def correlator():
    events = []
    capture = proof.Correlator(lambda pid, stamp: {'pid': pid, 'process': 'test', 'container_id': str(pid)}, events.append)
    return capture, events


def context(capture, tid, pid, token, now=1):
    capture.accept((tid, now, 'ctx_in', {'request': str(token)}))
    capture.accept((tid, now+.01, 'ctx_out', {'context': '1234', 'origin': str(pid)}))
    capture.accept((tid, now+.02, 'cb_in_0', {}))


def test_two_workers_keep_origins_and_disks_separate():
    capture, events = correlator()
    context(capture, 10, 100, 777)
    context(capture, 20, 200, 777)  # Same pointer in a different worker/address space.
    capture.accept((10, 1.03, 'backing_open', {'filename': '/mnt/disk1/private-name'}))
    capture.accept((20, 1.04, 'backing_open', {'filename': '/mnt/disk2/another-private-name'}))
    capture.accept((20, 1.05, 'backing_done', {'fd': '4'}))
    capture.accept((10, 1.06, 'backing_done', {'fd': '5'}))
    assert [(e['pid'], e['disk']) for e in events] == [(200, 'disk2'), (100, 'disk1')]
    assert 'private-name' not in str(events)
    assert all(not e['physical_spin_up_proven'] for e in events)


def test_callback_return_and_trace_loss_remove_stale_identity():
    capture, events = correlator()
    context(capture, 10, 100, 777)
    capture.accept((10, 1.03, 'backing_open', {'filename': '/mnt/disk1/a'}))
    capture.accept((10, 1.04, 'cb_out_0', {}))
    capture.accept((10, 1.05, 'backing_done', {'fd': '3'}))
    assert events == []
    context(capture, 10, 200, 888, now=2)
    capture.reset()
    capture.accept((10, 2.03, 'backing_open', {'filename': '/mnt/disk1/a'}))
    capture.accept((10, 2.04, 'backing_done', {'fd': '3'}))
    assert events == []


def test_failed_and_unmapped_opens_are_not_disk_matches():
    capture, events = correlator()
    context(capture, 10, 100, 777)
    for path, result in (('/mnt/disk1/a', '-2'), ('/mnt/cache/a', '3'), ('relative-name', '4')):
        capture.accept((10, 1.03, 'backing_open', {'filename': path}))
        capture.accept((10, 1.04, 'backing_done', {'fd': result}))
    assert events == []


def test_kernel_trace_format_parses_only_named_arguments():
    record = proof.parse_line(' shfs-123 [004] ...1 122.000100: backing_open: (do_sys_openat2+0x0) filename="/mnt/disk1/a file"')
    assert record == (123, 122.0001, 'backing_open', {'filename': '/mnt/disk1/a file'})
    assert proof.parse_line('unrelated header') is None


def test_missing_dispatch_symbol_never_attaches_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, 'shfs_threads', lambda proc: {123})
    with pytest.raises(ValueError, match='Required exported'):
        proof.capture(tmp_path/'library', {}, tmp_path/'tracing', tmp_path/'proc', 1)
    assert not (tmp_path/'tracing').exists()


def test_wrong_callback_return_invalidates_the_worker_scope():
    capture, events = correlator()
    context(capture, 10, 100, 777)
    capture.accept((10, 1.03, 'cb_out_1', {}))
    capture.accept((10, 1.04, 'backing_open', {'filename': '/mnt/disk1/a'}))
    capture.accept((10, 1.05, 'backing_done', {'fd': '3'}))
    assert events == []
    assert capture.counts['callback_mismatches'] == 1


def test_expired_request_context_is_not_reused():
    capture, events = correlator()
    capture.accept((10, 1, 'ctx_in', {'request': '777'}))
    capture.accept((10, 1.01, 'ctx_out', {'context': '1234', 'origin': '100'}))
    capture.accept((10, 3, 'cb_in_0', {}))
    capture.accept((10, 3.01, 'backing_open', {'filename': '/mnt/disk1/a'}))
    capture.accept((10, 3.02, 'backing_done', {'fd': '3'}))
    assert events == []


def test_setup_failure_unregisters_only_its_own_probe(tmp_path, monkeypatch, capsys):
    root = tmp_path/'tracing'
    (root/'instances').mkdir(parents=True)
    original = 'p:another_tool/keep /other/library:0x123\n'
    (root/'uprobe_events').write_text(original)
    monkeypatch.setattr(proof, 'shfs_threads', lambda proc: {123})
    original_write = Path.write_text
    def write(path, value, *args, **kwargs):
        if path.name == 'filter':
            raise OSError('simulated filter rejection')
        if 'instances' in path.parts:
            # Emulate kernel-created tracefs files without populating the directory.
            return len(value)
        return original_write(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    real_open = proof.os.open
    monkeypatch.setattr(proof.os, 'open', lambda path, flags: real_open(root/'uprobe_events', flags) if Path(path).name == 'free_buffer' else real_open(path, flags))
    with pytest.raises(OSError, match='simulated filter rejection'):
        proof.capture(Path('/library'), {'fuse_req_ctx': 123, 'fuse_fs_open': 456}, root, tmp_path/'proc', 1)
    lines = (root/'uprobe_events').read_text().splitlines()
    assert lines[0] == original.strip()
    created = lines[1].split()[0][2:]
    assert created.startswith('hddproof_') and created.endswith('/ctx_in')
    assert lines[2] == '-:'+created
    assert list((root/'instances').iterdir()) == []
    assert 'cleanup_complete' in capsys.readouterr().out
