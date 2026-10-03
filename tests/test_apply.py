#!/usr/bin/env python3
# SPDX-License-Identifier: 0BSD
"""Installer lifecycle tests using the pinned board file from an OpenWrt clone."""
import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('overlay', ROOT / 'scripts/apply.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
parser = argparse.ArgumentParser()
parser.add_argument('--openwrt', required=True, type=Path)
args, remaining = parser.parse_known_args()
pristine = subprocess.check_output(['git', 'show', overlay.SUPPORTED_COMMIT + ':' + overlay.DTS], cwd=args.openwrt)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='overlay-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        overlay.write(self.root / overlay.DTS, pristine)
        self.head = overlay.SUPPORTED_COMMIT
        real_run = overlay.run
        def run(*cmd, **kw):
            if cmd == ('git', 'rev-parse', 'HEAD'):
                return subprocess.CompletedProcess(cmd, 0, self.head + '\n', '')
            return real_run(*cmd, **kw)
        self.mock_run = patch.object(overlay, 'run', side_effect=run)
        self.mock_run.start()
        self.addCleanup(self.mock_run.stop)

    def invoke(self, *opts):
        with patch.object(sys, 'argv', ['apply.py', '--openwrt', str(self.root), *opts]), contextlib.redirect_stdout(io.StringIO()):
            overlay.main()

    def contents(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_lifecycle(self):
        initial = self.contents()
        self.invoke('--check')
        self.assertEqual(initial, self.contents())
        self.invoke()
        installed = self.contents()
        self.invoke()
        self.assertEqual(installed, self.contents())
        self.invoke('--profile', 'we826-experimental')
        self.assertTrue(list((self.root / overlay.DRIVERS).glob('999c-*')))
        self.invoke('--profile', 'we826')
        self.assertEqual(installed, self.contents())
        self.invoke('--unapply', '--check')
        self.assertEqual(installed, self.contents())
        self.invoke('--unapply')
        self.assertEqual(initial, self.contents())

    def test_local_edits_preserved(self):
        self.invoke()
        with (self.root / overlay.DTS).open('ab') as f:
            f.write(b'\n// user modification\n')
        modified = self.contents()
        with self.assertRaisesRegex(ValueError, 'Locally modified'):
            self.invoke('--unapply')
        self.assertEqual(modified, self.contents())

    def test_conflicting_patch_preserved(self):
        name = next((ROOT / 'mac80211-patches').glob('998-*')).name
        overlay.write(self.root / overlay.DRIVERS / name, b'unrelated patch\n')
        initial = self.contents()
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.invoke()
        self.assertEqual(initial, self.contents())

    def test_wrong_version(self):
        self.head = '0' * 40
        initial = self.contents()
        with self.assertRaisesRegex(ValueError, 'Only OpenWrt'):
            self.invoke()
        self.assertEqual(initial, self.contents())

    def test_failed_preflight_leaves_no_changes(self):
        overlay.write(self.root / overlay.DTS, b'not a device tree\n')
        initial = self.contents()
        with self.assertRaisesRegex(ValueError, 'preflight failed'):
            self.invoke()
        self.assertEqual(initial, self.contents())

    def test_preexisting_matching_patch_preserved(self):
        p = next((ROOT / 'mac80211-patches').glob('998-*'))
        overlay.write(self.root / overlay.DRIVERS / p.name, p.read_bytes())
        initial = self.contents()
        self.invoke()
        self.invoke('--unapply')
        self.assertEqual(initial, self.contents())

    def test_preexisting_experimental_not_silently_enabled(self):
        p = next((ROOT / 'mac80211-patches').glob('999a-*'))
        overlay.write(self.root / overlay.DRIVERS / p.name, p.read_bytes())
        initial = self.contents()
        with self.assertRaisesRegex(ValueError, 'Experimental patches predate'):
            self.invoke()
        self.assertEqual(initial, self.contents())

    def test_write_failure_rolls_back(self):
        initial = self.contents()
        real_write = overlay.write
        failed = False
        def write(path, data):
            nonlocal failed
            if path.name.startswith('998-') and not failed:
                failed = True
                raise OSError('simulated write failure')
            real_write(path, data)
        with patch.object(overlay, 'write', side_effect=write):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertTrue(failed)
        self.assertEqual(initial, self.contents())


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
