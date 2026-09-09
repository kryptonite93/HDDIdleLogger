import importlib.util
import json
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
            raise OSError(22, 'Invalid argument')
        if 'instances' in path.parts:
            # Emulate kernel-created tracefs files without populating the directory.
            return len(value)
        return original_write(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    real_open = proof.os.open
    monkeypatch.setattr(proof.os, 'open', lambda path, flags: real_open(root/'uprobe_events', flags) if Path(path).name == 'free_buffer' else real_open(path, flags))
    with pytest.raises(OSError, match='Invalid argument'):
        proof.capture(Path('/library'), {'fuse_req_ctx': 123, 'fuse_fs_open': 456}, root, tmp_path/'proc', 1)
    lines = (root/'uprobe_events').read_text().splitlines()
    assert lines[0] == original.strip()
    created = lines[1].split()[0][2:]
    assert created.startswith('hddproof_') and created.endswith('/ctx_in')
    assert lines[2] == '-:'+created
    assert list((root/'instances').iterdir()) == []
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    error = next(item for item in output if item['state'] == 'proof_error')
    assert error['errno'] == 22
    assert error['operation'] == 'write_setting'
    assert error['path'].endswith('/ctx_in/filter')
    assert error['requested'] == 'common_pid == 123'
    assert output[-1]['state'] == 'cleanup_complete'


def test_diagnostic_reads_only_own_kernel_errors_without_clearing_log(tmp_path):
    group = 'hddproof_test'
    instance = tmp_path/'instances'/group
    instance.mkdir(parents=True)
    content = ('[ 1.000] trace_uprobe: error: unrelated error\n'
               '  Command: p:other_tool/event /private/library:0\n'
               '[ 2.000] trace_uprobe: error: invalid fetch argument\n'
               '  Command: r:hddproof_test/ctx_out /host/libfuse.so:0x123 origin=+8($retval):u32\n'
               '                                                  ^\n')
    (tmp_path/'error_log').write_text(content)
    result = proof.failure_diagnostics(tmp_path, instance, group, {'path': str(tmp_path/'uprobe_events')})
    assert 'invalid fetch argument' in str(result)
    assert '/private/library' not in str(result)
    assert (tmp_path/'error_log').read_text() == content


def test_filter_rejection_preserves_kernel_feedback(tmp_path):
    group = 'hddproof_test'
    instance = tmp_path/'instances'/group
    event = instance/'events'/group/'ctx_in'
    event.mkdir(parents=True)
    (event/'filter').write_text('common_pid == 123\n^\nparse_error: rejected predicate')
    result = proof.failure_diagnostics(tmp_path, instance, group, {'path': str(event/'filter')})
    assert 'rejected predicate' in result['setting_feedback']


def test_probe_commands_preserve_existing_definitions_and_do_not_create_controls(tmp_path):
    path = tmp_path/'uprobe_events'
    path.write_text('p:other_tool/keep /library:0x123\n')
    proof.append_probe_command(path, 'p:hddproof_test/ctx_in /library:0x456')
    proof.append_probe_command(path, '-:hddproof_test/ctx_in')
    assert path.read_text().splitlines() == [
        'p:other_tool/keep /library:0x123',
        'p:hddproof_test/ctx_in /library:0x456',
        '-:hddproof_test/ctx_in',
    ]
    missing = tmp_path/'missing_control'
    with pytest.raises(FileNotFoundError):
        proof.append_probe_command(missing, 'p:hddproof_test/ctx_in /library:0x456')
    assert not missing.exists()


def test_host_burst_cannot_hide_later_container_opens():
    events = []
    identities = {
        100: {'pid': 100, 'process': 'emhttpd', 'container_id': None},
        200: {'pid': 200, 'process': 'Plex Media', 'container_id': 'test-plex-id'},
    }
    capture = proof.Correlator(lambda pid, stamp: identities[pid], events.append)
    context(capture, 10, 100, 777)
    for _ in range(1176):
        capture.accept((10, 1.03, 'backing_open', {'filename': '/mnt/disk1/private-name'}))
        capture.accept((10, 1.04, 'backing_done', {'fd': '3'}))
    context(capture, 20, 200, 888, now=2)
    for _ in range(22):
        capture.accept((20, 2.03, 'backing_open', {'filename': '/mnt/disk7/private-name'}))
        capture.accept((20, 2.04, 'backing_done', {'fd': '4'}))
    assert any(row['container_id'] == 'test-plex-id' for row in events)
    summaries = capture.source_summaries()
    assert summaries[0]['container_id'] == 'test-plex-id'
    assert summaries[0]['disk'] == 'disk7'
    assert summaries[0]['matched_backing_opens'] == 22
    assert summaries[1]['matched_backing_opens'] == 1176
    assert all(not row['physical_spin_up_proven'] for row in summaries)
    assert 'private-name' not in str(summaries)
    assert len(events) <= 25


def test_host_summary_overflow_leaves_room_for_container_results():
    capture = proof.Correlator(lambda pid, stamp: {
        'pid': pid, 'process': f'process{pid}', 'container_id': 'test-container' if pid == 1000 else None,
    }, lambda row: None)
    for index, pid in enumerate([*range(1, 258), 1000]):
        now = index+1
        context(capture, 10, pid, pid, now=now)
        capture.accept((10, now+.03, 'backing_open', {'filename': '/mnt/disk1/a'}))
        capture.accept((10, now+.04, 'backing_done', {'fd': '3'}))
    rows = capture.source_summaries()
    assert len(rows) == 257
    assert rows[0]['container_id'] == 'test-container'
    assert capture.counts['host_summary_omitted_opens'] == 1
    assert capture.counts['matched_backing_opens'] == sum(row['matched_backing_opens'] for row in rows)+1
