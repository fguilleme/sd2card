import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import mock_open, patch
from sdbackup.devices import discover, assert_present, known_sd_reader, usb_reader_counts, SafetyError


def disk(path='/dev/sda', mount=None):
    return dict(path=path, type='disk', tran='usb', ro=False, serial='serial',
                size=64000000000, model='Reader', mountpoints=[None],
                children=[dict(path=path+'1', type='part', ro=False,
                               uuid='uuid', fstype='vfat', label='TEST',
                               size=63000000000, mountpoints=[mount])])


class DevicesTests(unittest.TestCase):
    def scan(self, disks, props='ID_DRIVE_FLASH_SD=1'):
        def command(args, **kwargs):
            return json.dumps({'blockdevices': disks}).encode() if args[0] == 'lsblk' else props
        with patch('sdbackup.devices.subprocess.check_output', side_effect=command), \
             patch('builtins.open', mock_open(read_data='123')):
            return discover()

    def test_discovery_explicitly_requests_partition_tree_and_sysfs(self):
        media_disk = disk()
        part = media_disk['children'][0]
        def command(args, **kwargs):
            if args[0] == 'lsblk':
                # lsblk emits a flat list without NAME or --tree.
                devices = [media_disk] if '--tree' in args else [dict(media_disk, children=[]), part]
                return json.dumps({'blockdevices': devices}).encode()
            self.assertIn('--path', args)
            self.assertIn('/sys/class/block/sda', args)
            return 'ID_DRIVE_FLASH_SD=1'
        with patch('sdbackup.devices.subprocess.check_output', side_effect=command), \
             patch('builtins.open', mock_open(read_data='123')):
            self.assertEqual(len(discover()), 1)

    def test_registered_reader_requires_matching_device_and_port(self):
        reader = {'ID_VENDOR_ID': '349c', 'ID_MODEL_ID': '0418',
                  'ID_SERIAL_SHORT': '1234', 'ID_PATH_TAG': 'usb-port-2'}
        self.assertTrue(known_sd_reader(reader, [reader]))
        for key in reader:
            self.assertFalse(known_sd_reader(dict(reader, **{key: 'different'}), [reader]))
        self.assertFalse(known_sd_reader({}, [{}]))
        self.assertFalse(known_sd_reader(reader, []))

    def test_known_reader_can_move_ports_when_its_identity_is_unique(self):
        reader = {'ID_VENDOR_ID': '349c', 'ID_MODEL_ID': '0418',
                  'ID_SERIAL_SHORT': '1234', 'ID_PATH_TAG': 'original-port'}
        moved = dict(reader, ID_PATH_TAG='another-port')
        key = ('349c', '0418', '1234')
        self.assertTrue(known_sd_reader(moved, [reader], {key: 1}))
        self.assertFalse(known_sd_reader(moved, [reader], {key: 2}))
        self.assertFalse(known_sd_reader(moved, [reader], {}))
        self.assertTrue(known_sd_reader(reader, [reader], {key: 2}))
        wrong = dict(moved, ID_SERIAL_SHORT='different-reader')
        self.assertFalse(known_sd_reader(wrong, [reader], {('349c', '0418', 'different-reader'): 1}))

    def test_usb_counts_include_duplicate_readers_without_block_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('1-1.1', '1-1.2'):
                device = root/name
                device.mkdir()
                for field, value in (('idVendor', '349c'), ('idProduct', '0418'), ('serial', '1234')):
                    (device/field).write_text(value+'\n')
            (root/'1-1.1:1.0').mkdir()
            self.assertEqual(usb_reader_counts(root), {('349c', '0418', '1234'): 2})

    def test_system_disk_excluded(self):
        self.assertEqual(self.scan([disk(mount='/')]), [])
        self.assertEqual(self.scan([disk(mount='/boot/firmware')]), [])
        self.assertEqual(self.scan([disk(mount='[SWAP]')]), [])

    def test_single_external_partition_supported(self):
        media = self.scan([disk(mount='/media/pi/TEST')])
        self.assertEqual(len(media), 1)
        self.assertTrue(media[0].sd)

    def test_unknown_reader_never_guessed_as_sd(self):
        self.assertFalse(self.scan([disk()], props='ID_BUS=usb')[0].sd)

    def test_multiple_partitions_refused(self):
        item = disk()
        item['children'].append(dict(item['children'][0], path='/dev/sda2'))
        self.assertEqual(self.scan([item]), [])

    def test_unpartitioned_refused(self):
        item = disk()
        item['children'] = []
        self.assertEqual(self.scan([item]), [])

    def test_replaced_device_same_name_refused(self):
        original = self.scan([disk()])[0]
        changed = disk()
        changed['serial'] = 'replacement'
        replacement = self.scan([changed])[0]
        with patch('sdbackup.devices.discover', return_value=[replacement]):
            with self.assertRaises(SafetyError):
                assert_present(original)


if __name__ == '__main__':
    unittest.main()
