import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from sdbackup.copying import Backup, Cancelled
from sdbackup.devices import SafetyError


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / 'sd'
        self.dst = self.root / 'usb'
        self.src.mkdir()
        self.dst.mkdir()
        (self.src / 'photos').mkdir()
        (self.src / 'vide').mkdir()
        (self.src / 'photos' / 'été.jpg').write_bytes(b'image\0' * 1000)
        (self.src / 'zero').touch()

    def backup(self, **kwargs):
        return Backup(self.src, self.dst, **kwargs)

    def test_verified_copy_manifest_and_empty_directories(self):
        backup = self.backup()
        folder = backup.run()
        self.assertFalse(folder.name.endswith('.incomplet'))
        self.assertEqual((folder / 'contenu/photos/été.jpg').read_bytes(),
                         (self.src / 'photos/été.jpg').read_bytes())
        self.assertTrue((folder / 'contenu/vide').is_dir())
        self.assertEqual(len(json.loads((folder / 'verification.json').read_text())['files']), 2)
        backup.verify()

    def test_corruption_blocks_verification(self):
        backup = self.backup()
        folder = backup.run()
        (folder / 'contenu/photos/été.jpg').write_bytes(b'broken' * 1000)
        with self.assertRaises(SafetyError):
            backup.verify()

    def test_source_change_blocks_verification(self):
        backup = self.backup()
        backup.run()
        (self.src / 'new').touch()
        with self.assertRaises(SafetyError):
            backup.verify()

    def test_extra_destination_file_blocks_verification(self):
        backup = self.backup()
        folder = backup.run()
        (folder / 'contenu/new').touch()
        with self.assertRaises(SafetyError):
            backup.verify()

    def test_cancel_keeps_source_and_incomplete_copy(self):
        event = threading.Event()
        def progress(*_):
            event.set()
        backup = self.backup(cancel=event, progress=progress)
        with self.assertRaises(Cancelled):
            backup.run()
        self.assertTrue((self.src / 'photos/été.jpg').exists())
        self.assertTrue(backup.folder.name.endswith('.incomplet'))
        self.assertFalse((backup.folder / 'verification.json').exists())

    def test_symlink_refused(self):
        (self.src / 'link').symlink_to('/etc/passwd')
        with self.assertRaises(SafetyError):
            self.backup().run()
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_full_disk_refused_before_writing(self):
        with patch('sdbackup.copying.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(SafetyError):
                self.backup().run()
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_removed_device_refused(self):
        def guard():
            raise SafetyError('retiré')
        with self.assertRaises(SafetyError):
            self.backup(guard=guard).run()
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_sync_failure_never_marks_complete(self):
        backup = self.backup()
        with patch('sdbackup.copying.sync_filesystem', side_effect=OSError('I/O')):
            with self.assertRaises(OSError):
                backup.run()
        self.assertTrue(backup.folder.name.endswith('.incomplet'))

    def test_repeated_backups_do_not_overwrite(self):
        one = self.backup().run()
        two = self.backup().run()
        self.assertNotEqual(one, two)
        self.assertTrue(one.exists())


if __name__ == '__main__':
    unittest.main()
