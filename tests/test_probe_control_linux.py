"""Exercise Linux seq_file writes without attaching probes or needing root."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('request_proof', Path(__file__).parents[1]/'scripts/fuse-request-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux seq_file semantics')
class ProbeControlLinuxTest(unittest.TestCase):
    def test_command_write_works_on_seq_file_without_seeking(self):
        # Like uprobe_events, this writable proc file uses seq_lseek, which
        # rejects SEEK_END. Only this test process's name changes, then restores.
        path = Path('/proc/self/comm')
        original = path.read_bytes()
        try:
            proof.append_probe_command(path, 'hdd-proof-check')
            self.assertEqual(path.read_text().strip(), 'hdd-proof-check')
        finally:
            fd = os.open(path, os.O_WRONLY)
            try:
                os.write(fd, original)
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
