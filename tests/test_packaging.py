"""Synthetic release-boundary tests; never use real secrets or experiment files."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_skill as package


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        (self.root / 'SKILL.md').write_text('metadata:\n  version: "0.0.0-test"\n', encoding='utf-8')
        (self.root / 'LICENSE').write_text('Synthetic license', encoding='utf-8')
        (self.root / 'public.md').write_text('Approved synthetic fixture', encoding='utf-8')
        self.names = ['SKILL.md', 'LICENSE', 'release_files.json', 'public.md']
        self.save_list()
        self.output = Path(self.temp.name) / 'package'

    def save_list(self):
        (self.root / 'release_files.json').write_text(json.dumps(dict(schema_version=1, files=self.names)), encoding='utf-8')

    def build(self):
        with mock.patch.object(package, 'ROOT', self.root):
            return package.build(self.output)

    def test_unlisted_backups_data_and_environment_never_ship(self):
        for name in ['references/local_environment.backup.json', 'references/research_notes.md',
                     'docs/private_case.png', 'tests/.env', 'examples/measurements.csv']:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'SYNTHETIC PRIVATE FIXTURE')
        result = self.build()
        with zipfile.ZipFile(result['archive']) as z:
            self.assertEqual(set(z.namelist()), {'worm-fluorescence/' + n for n in self.names})
        self.assertEqual(result['files_verified'], len(self.names))

    def test_approved_content_paths_and_credentials_fail_without_echo(self):
        examples = ['D' + ':/private-study/input.tif',
                    json.dumps({'path': 'E' + ':\\private-study\\input.tif'}),
                    '/' + 'home/researcher/private/config',
                    'gh' + 'p_' + 'X' * 30,
                    'password = ' + repr('X' * 24)]
        for value in examples:
            with self.subTest(kind=examples.index(value)):
                (self.root / 'public.md').write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    self.build()
                self.assertNotIn(value, str(caught.exception))
                self.assertFalse(self.output.exists())

    def test_invalid_allowlist_entries_and_missing_files_fail(self):
        for name in ['../outside.md', 'docs/case.png', 'references/local_settings.json',
                     'tests/.env', 'missing.py']:
            with self.subTest(name=name):
                self.names = ['SKILL.md', 'LICENSE', 'release_files.json', name]
                self.save_list()
                with self.assertRaises(ValueError): self.build()
                self.assertFalse(self.output.exists())

    def test_non_utf8_text_and_duplicate_entries_fail(self):
        (self.root / 'public.md').write_bytes(b'\xff\x00')
        with self.assertRaises(ValueError): self.build()
        (self.root / 'public.md').write_text('Synthetic', encoding='utf-8')
        self.names.append('public.md');self.save_list()
        with self.assertRaises(ValueError): self.build()

    def test_added_archive_payload_is_rejected(self):
        result = self.build()
        manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        with zipfile.ZipFile(result['archive'], 'a') as z:
            z.writestr('worm-fluorescence/unlisted.md', 'Synthetic unexpected payload')
        with self.assertRaises(ValueError): package.verify_archive(result['archive'], manifest)

    def test_changed_archive_bytes_are_rejected(self):
        result = self.build()
        manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        path = Path(self.temp.name) / 'tampered.zip'
        with zipfile.ZipFile(result['archive']) as original, zipfile.ZipFile(path, 'w') as changed:
            for name in original.namelist():
                changed.writestr(name, b'Changed synthetic text' if name.endswith('/public.md') else original.read(name))
        with self.assertRaises(ValueError): package.verify_archive(path, manifest)

    def test_existing_output_is_preserved(self):
        self.output.mkdir();sentinel=self.output/'keep.txt';sentinel.write_text('keep',encoding='utf-8')
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')

    def test_repository_approved_text_passes_the_same_content_scan(self):
        payload = package.public_files()
        self.assertIn('scripts/package_skill.py', payload)
        self.assertIn('scripts/delivery_preferences.py', payload)


if __name__ == '__main__': unittest.main()
