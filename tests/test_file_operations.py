"""Real Windows rename regression with a synthetic path exceeding MAX_PATH."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from file_operations import filesystem_path, replace_with_retry


class FileOperationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows long-filename regression')
    def test_long_archive_destination_preserves_file_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'synthetic_source.bin'
            source.write_bytes(b'SYNTHETIC archive fixture')
            parent = root
            while len(str(parent)) < 232:
                size = min(40, 242 - len(str(parent)) - 1)
                parent = parent / ('x' * size)
            parent.mkdir(parents=True)
            destination = parent / ('archived_' + 'y' * 40 + '.bin')
            self.assertGreater(len(str(destination)), 260)
            try:
                replace_with_retry(source, destination)
                self.assertFalse(source.exists())
                with open(filesystem_path(destination), 'rb') as f:
                    self.assertEqual(f.read(), b'SYNTHETIC archive fixture')
            finally:
                if os.path.exists(filesystem_path(destination)):
                    os.unlink(filesystem_path(destination))


if __name__ == '__main__': unittest.main()
