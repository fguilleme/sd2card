import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('appearance', Path(__file__).resolve().parents[1] / 'install-appearance-root.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PROJECT = Path(__file__).resolve().parents[1]


class AppearanceTests(unittest.TestCase):
    def test_cmdline_preserves_boot_and_is_idempotent(self):
        old = 'console=tty1 root=PARTUUID=abcd-02 rootwait quiet splash\n'
        new = module.cmdline(old)
        self.assertIn('root=PARTUUID=abcd-02', new)
        self.assertIn('console=tty1', new)
        self.assertIn('logo.nologo', new)
        self.assertEqual(module.cmdline(new), new)
        self.assertEqual(len(new.splitlines()), 1)

    def test_invalid_cmdline_rejected(self):
        for text in ('quiet splash', 'root=/dev/sda2\nquiet splash'):
            with self.assertRaises(RuntimeError):
                module.cmdline(text)

    def test_firmware_config_is_idempotent_and_preserves_display(self):
        original = '[all]\ndtoverlay=tft35a:rotate=90\n'
        updated = module.firmware_config(original)
        self.assertTrue(updated.startswith(original))
        self.assertIn('disable_splash=1', updated)
        self.assertEqual(module.firmware_config(updated), updated)

    def fixture(self, root):
        files = {'boot/firmware/config.txt': '[all]\ndtoverlay=tft35a:rotate=90\n',
                 'boot/firmware/cmdline.txt': 'root=PARTUUID=abcd-02 quiet splash\n',
                 'boot/firmware/initramfs8': 'original-initramfs',
                 'boot/initrd.img-test-rpi-v8': 'original-initrd',
                 'etc/plymouth/plymouthd.conf': '[Daemon]\nTheme=pix\n'}
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return files

    def test_install_and_backup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module.os, 'sync'):
            root = Path(directory)
            original = self.fixture(root)
            calls = []
            module.install(PROJECT, root=root, run=lambda args, **kw: calls.append(args))
            self.assertEqual(calls[0][1], 'sdbackup')
            self.assertEqual(calls[1][1:], ['-u', '-k', 'all'])
            self.assertTrue((root / 'usr/local/share/sdbackup/background.png').is_file())
            backup = next((root / 'var/backups/sdbackup-appearance').iterdir())
            for relative, content in original.items():
                self.assertEqual((backup / relative).read_text(), content)

    def test_rebuild_failure_restores_previous_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module.os, 'sync'):
            root = Path(directory)
            original = self.fixture(root)
            def fail(args, **kwargs):
                if 'update-initramfs' in args[0]:
                    (root / 'boot/firmware/initramfs8').write_text('bad-new-image')
                    raise subprocess.CalledProcessError(1, args)
            with self.assertRaises(subprocess.CalledProcessError):
                module.install(PROJECT, root=root, run=fail)
            for relative, content in original.items():
                self.assertEqual((root / relative).read_text(), content)
            self.assertFalse((root / 'usr/local/share/sdbackup/background.png').exists())
            self.assertFalse((root / 'usr/share/plymouth/themes/sdbackup/sdbackup.plymouth').exists())


if __name__ == '__main__':
    unittest.main()
