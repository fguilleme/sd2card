"""Test destructive-operation ordering without touching any real device."""
import queue
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from sdbackup.operations import erase_card
from sdbackup.copying import Cancelled
from sdbackup.devices import SafetyError


class AcknowledgedQueue(queue.Queue):
    def put(self, item):
        if item[0] == 'formatting':
            item[1].set()
        super().put(item)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace()
        self.app.pair = (Mock(name='SD'), Mock(name='USB'))
        self.app.backup = Mock()
        self.app.cancel = threading.Event()
        self.app.events = AcknowledgedQueue()
        self.app.erase = lambda: erase_card(
            *self.app.pair, self.app.backup, self.app.cancel,
            lambda kind, data: self.app.events.put((kind, data)))
        self.storage = Mock()
        self.storage.validate_pair.return_value = ('sd-drive', 'usb-drive')
        self.trace = Mock()
        self.trace.attach_mock(self.app.backup, 'backup')
        self.trace.attach_mock(self.storage, 'storage')
        for target, kwargs in [
            ('sdbackup.operations.Storage', {'return_value': self.storage}),
            ('sdbackup.operations.assert_present', {}),
            ('sdbackup.operations.sync_filesystem', {})]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_verify_and_both_unmounts_precede_format(self):
        self.app.erase()
        names = [c[0] for c in self.trace.mock_calls]
        self.assertLess(names.index('backup.verify'), names.index('storage.format_sd'))
        unmounts = [i for i, n in enumerate(names) if n == 'storage.unmount']
        self.assertEqual(len(unmounts), 2)
        self.assertLess(max(unmounts), names.index('storage.format_sd'))
        self.assertLess(names.index('storage.format_sd'), names.index('storage.poweroff'))
        self.assertEqual(self.storage.poweroff.call_count, 2)

    def test_failed_verification_never_formats_or_ejects(self):
        self.app.backup.verify.side_effect = SafetyError('corrompu')
        with self.assertRaises(SafetyError):
            self.app.erase()
        self.storage.format_sd.assert_not_called()
        self.storage.poweroff.assert_not_called()

    def test_failed_unmount_never_formats(self):
        self.storage.unmount.side_effect = OSError('occupé')
        with self.assertRaises(OSError):
            self.app.erase()
        self.storage.format_sd.assert_not_called()

    def test_cancel_before_format_preserves_sd(self):
        self.storage.unmount.side_effect = lambda _: self.app.cancel.set()
        with self.assertRaises(Cancelled):
            self.app.erase()
        self.storage.format_sd.assert_not_called()

    def test_reader_preflight_failure_never_formats(self):
        self.storage.validate_pair.side_effect = SafetyError('lecteur partagé')
        with self.assertRaises(SafetyError):
            self.app.erase()
        self.storage.format_sd.assert_not_called()


if __name__ == '__main__':
    unittest.main()
