import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('console_boot', Path(__file__).resolve().parents[1] / 'disable-x.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConsoleBootTests(unittest.TestCase):
    def test_profile_preserves_framebuffer_and_other_commands(self):
        text = 'export FRAMEBUFFER=/dev/fb1\nstartx 2> /tmp/x.log\necho startx\n'
        updated = module.console_profile(text)
        self.assertIn('export FRAMEBUFFER=/dev/fb1\n', updated)
        self.assertIn('echo startx\n', updated)
        self.assertNotIn('\nstartx ', updated)
        self.assertEqual(module.console_profile(updated), updated)

    def run_scenario(self, directory, fail=False):
        root = Path(directory)
        profile = root / 'bash_profile'
        original = b'export FRAMEBUFFER=/dev/fb1\nstartx 2> /tmp/log_output.txt\n'
        profile.write_bytes(original)
        backups = root / 'backups'
        calls = []
        def control(*args, **kwargs):
            calls.append(args)
            if args == ('get-default',):
                return SimpleNamespace(stdout='multi-user.target\n')
            if args[0] == 'is-enabled':
                return SimpleNamespace(stdout='enabled\n')
            if fail and args == ('disable', 'lightdm.service'):
                raise subprocess.CalledProcessError(1, args)
            return SimpleNamespace(stdout='')
        with patch.object(module, 'PROFILE', profile), patch.object(module, 'BACKUPS', backups), \
             patch.object(module, 'systemctl', side_effect=control), patch.object(module.os, 'sync'), \
             patch('builtins.print'):
            if fail:
                with self.assertRaises(subprocess.CalledProcessError):
                    module.disable()
                self.assertEqual(profile.read_bytes(), original)
                self.assertIn(('enable', 'lightdm.service'), calls)
            else:
                module.disable()
                self.assertNotIn('\nstartx ', profile.read_text())
                saved = next(backups.iterdir())
                self.assertEqual((saved / 'bash_profile').read_bytes(), original)
                module.restore(saved)
                self.assertEqual(profile.read_bytes(), original)
        self.assertFalse(any(arg in ('stop', 'isolate', 'reboot', '--now') for call in calls for arg in call))

    def test_disable_backs_up_and_can_restore_without_ending_session(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_scenario(directory)

    def test_failure_restores_original_profile_and_service_state(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_scenario(directory, fail=True)


if __name__ == '__main__':
    unittest.main()
