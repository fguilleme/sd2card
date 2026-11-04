import importlib.util
import errno
import json
from pathlib import Path
import struct
import tempfile
import threading
from unittest.mock import Mock, patch
import unittest

from PIL import Image

from sdbackup.devices import Media
from sdbackup.fbdev import AffineCalibration, Calibration, Framebuffer, FixedInfo, Console, TouchEvents, encode_pixels
from sdbackup.fb_app import FramebufferApp


def media(path, sd=False):
    return Media(path, path+'1', (path, 'generation', 'serial', 32000000000, path+'-uuid'),
                 path+'-uuid', 'CARTE' if sd else 'SAUVEGARDES', 32000000000, 'vfat', sd)


class FramebufferTests(unittest.TestCase):
    def open_simulated(self, driver=b'fb_ili9486', nonstd=1, grayscale=0, red=(11, 5)):
        fixed = FixedInfo()
        fixed.id, fixed.type, fixed.visual = driver, 0, 2
        fixed.smem_len, fixed.line_length = 480*320*2, 960
        values = [0]*40
        values[:8] = [480, 320, 480, 320, 0, 0, 16, grayscale]
        values[8:10], values[11:13], values[14:16] = red, (5, 6), (0, 5)
        values[20] = nonstd
        def ioctl(fd, request, buffer, mutate):
            buffer[:] = bytes(fixed) if request == 0x4602 else struct.pack('=40I', *values)
        patches = [patch('sdbackup.fbdev.os.open', return_value=123),
                   patch('sdbackup.fbdev.os.close'),
                   patch('sdbackup.fbdev.fcntl.ioctl', side_effect=ioctl),
                   patch('sdbackup.fbdev.mmap.mmap')]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return Framebuffer()

    def test_ili9486_rgb565_nonstd_marker_is_supported(self):
        fb = self.open_simulated()
        self.assertEqual((fb.width, fb.height, fb.bits), (480, 320, 16))
        self.assertEqual(fb.red, (11, 5))
        self.assertEqual(fb.stride, 960)
        fb.close()

    def test_standard_truecolor_remains_supported(self):
        fb = self.open_simulated(driver=b'other', nonstd=0)
        self.assertEqual(fb.bits, 16)
        fb.close()

    def test_unknown_nonstandard_mode_still_rejected_with_diagnostics(self):
        with self.assertRaisesRegex(ValueError, 'nonstd=1'):
            self.open_simulated(driver=b'unknown')

    def test_ili9486_exception_does_not_allow_other_pixel_layouts(self):
        with self.assertRaises(ValueError):
            self.open_simulated(red=(10, 5))
        with self.assertRaises(ValueError):
            self.open_simulated(grayscale=1)

    def test_dedicated_framebuffer_does_not_change_ssh_terminal_vt_mode(self):
        fb = Mock()
        fb.separate_from_console.return_value = True
        with patch('sdbackup.fbdev.os.open', return_value=123), \
             patch('sdbackup.fbdev.os.close') as close, \
             patch('sdbackup.fbdev.termios.tcgetattr', return_value=['settings']), \
             patch('sdbackup.fbdev.termios.tcsetattr'), \
             patch('sdbackup.fbdev.tty.setcbreak'), \
             patch('sdbackup.fbdev.fcntl.ioctl') as ioctl:
            console = Console(fb)
            self.assertEqual(console.fd, 123)
            self.assertIsNone(console.mode)
            console.close()
            ioctl.assert_not_called()
            close.assert_called_once_with(123)

    def test_dedicated_framebuffer_can_run_without_controlling_terminal(self):
        fb = Mock()
        fb.separate_from_console.return_value = True
        with patch('sdbackup.fbdev.os.open', side_effect=OSError(errno.ENXIO, 'No terminal')):
            console = Console(fb)
            self.assertIsNone(console.fd)
            console.close()

    def test_framebuffer_console_mapping_must_prove_display_is_separate(self):
        fb = Framebuffer.__new__(Framebuffer)
        fb.path, fb.fd = '/dev/fb1', 123
        with patch('sdbackup.fbdev.Path.read_text', return_value='tty1\n'), \
             patch('sdbackup.fbdev.fcntl.ioctl') as ioctl:
            def mapping(fd, request, data, mutate):
                data[:] = struct.pack('=II', 1, 0)
            ioctl.side_effect = mapping
            self.assertTrue(fb.separate_from_console())
            def shared(fd, request, data, mutate):
                data[:] = struct.pack('=II', 1, 1)
            ioctl.side_effect = shared
            self.assertFalse(fb.separate_from_console())
            ioctl.side_effect = OSError('mapping unavailable')
            self.assertFalse(fb.separate_from_console())

    def test_ssh_terminal_is_closed_and_reports_console_requirement(self):
        with patch('sdbackup.fbdev.os.open', return_value=123), \
             patch('sdbackup.fbdev.os.close') as close, \
             patch('sdbackup.fbdev.termios.tcgetattr', return_value=['settings']), \
             patch('sdbackup.fbdev.termios.tcsetattr'), \
             patch('sdbackup.fbdev.fcntl.ioctl', side_effect=OSError(errno.ENOTTY, 'Inappropriate ioctl')):
            with self.assertRaisesRegex(RuntimeError, 'SSH'):
                Console()
            close.assert_called_once_with(123)

    def test_rgb565_primary_colours(self):
        image = Image.new('RGB', (3, 1))
        image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        self.assertEqual(encode_pixels(image, 16, (11, 5), (5, 6), (0, 5)), struct.pack('=3H', 0xf800, 0x07e0, 0x001f))

    def test_row_padding_and_offsets_are_respected(self):
        fb = Framebuffer.__new__(Framebuffer)
        fb.width = fb.height = 2
        fb.bits, fb.red, fb.green, fb.blue = 16, (11, 5), (5, 6), (0, 5)
        fb.rowbytes, fb.stride, fb.xoffset, fb.start = 4, 8, 1, 10
        fb.memory = bytearray(b'Z' * 32)
        fb.show(Image.new('RGB', (2, 2), 'red'))
        expected = bytearray(b'Z' * 32)
        expected[10:14] = expected[18:22] = struct.pack('=2H', 0xf800, 0xf800)
        self.assertEqual(fb.memory, expected)

    def test_swapped_inverted_touch_calibration(self):
        calibration = Calibration()
        self.assertEqual(calibration.map(3936, 268), (0, 0))
        self.assertEqual(calibration.map(227, 3880), (479, 319))
        self.assertEqual(calibration.map(10000, -10000), (0, 0))

    def test_evdev_click_requires_down_and_release_reports(self):
        touch = TouchEvents()
        touch.feed(1, 330, 1)
        touch.feed(3, 0, 3936)
        touch.feed(3, 1, 268)
        self.assertEqual(touch.feed(0, 0, 0), ('down', (0, 0)))
        touch.feed(1, 330, 0)
        self.assertEqual(touch.feed(0, 0, 0), ('up', (0, 0)))
        self.assertIsNone(touch.feed(0, 0, 0))

    def test_evdev_overflow_cancels_contact_until_release(self):
        touch = TouchEvents()
        touch.feed(1, 330, 1)
        touch.feed(3, 0, 500)
        touch.feed(3, 1, 500)
        touch.feed(0, 0, 0)
        self.assertEqual(touch.feed(0, 3, 0), ('cancel', None))
        self.assertIsNone(touch.feed(0, 0, 0))
        self.assertIsNone(touch.feed(0, 0, 0))
        touch.feed(1, 330, 0)
        self.assertIsNone(touch.feed(0, 0, 0))
        touch.feed(1, 330, 1)
        self.assertEqual(touch.feed(0, 0, 0)[0], 'down')

    def test_affine_calibration_validates_independent_points(self):
        raw = [(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)]
        screen = [(48, 48), (431, 48), (431, 271), (48, 271), (240, 160)]
        calibration = AffineCalibration.fit(raw, screen)
        self.assertEqual(calibration.map(100, 100), (431, 271))
        with self.assertRaises(ValueError):
            AffineCalibration.fit(raw[:3]+[(80, 80)]+raw[4:], screen)
        with self.assertRaises(ValueError):
            AffineCalibration.fit([(0, 0)]*3, screen[:3])


class NativeUIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = FramebufferApp(Path(self.temp.name)/'touch.json')
        self.source, self.target = media('/dev/sda', True), media('/dev/sdb')

    def test_detection_selects_known_sd_and_other_disk(self):
        self.app.receive_media([self.source, self.target])
        self.assertEqual(self.app.source, self.source)
        self.assertEqual(self.app.target, self.target)
        self.assertEqual(self.app.render().size, (480, 320))
        self.assertTrue(next(b for b in self.app.buttons if b.key == 'copy').enabled)

    def test_removal_invalidates_backup_and_erase_confirmation(self):
        self.app.receive_media([self.source, self.target])
        self.app.backup = Mock()
        self.app.state = 'verified'
        self.app.pair = (self.source, self.target)
        self.app.confirm_erase()
        self.app.receive_media([self.target])
        self.assertIsNone(self.app.backup)
        self.assertEqual(self.app.page, 'main')
        self.assertNotIn('erase', [b.key for b in self.app.render() and self.app.buttons])

    def test_erase_acknowledged_only_after_screen_is_shown(self):
        acknowledgement = threading.Event()
        self.app.busy = self.app.cancellable = True
        self.app.emit('formatting', acknowledgement)
        self.app.drain()
        self.assertFalse(self.app.cancellable)
        self.assertFalse(acknowledgement.is_set())
        self.app.render()
        self.assertFalse(acknowledgement.is_set())
        self.app.acknowledge_render()
        self.assertTrue(acknowledgement.is_set())

    def test_drag_outside_button_does_not_activate(self):
        self.app.receive_media([self.source, self.target])
        self.app.start_copy = Mock()
        self.app.render()
        self.app.handle_touch('down', (200, 230))
        self.app.handle_touch('up', (0, 0))
        self.app.start_copy.assert_not_called()

    def test_destructive_action_requires_separate_confirmation(self):
        self.app.receive_media([self.source, self.target])
        self.app.state = 'verified'
        self.app.backup = Mock()
        self.app.pair = (self.source, self.target)
        self.app.erase = Mock()
        self.app.render()
        self.app.handle_touch('down', (200, 230))
        self.app.handle_touch('up', (200, 230))
        self.assertEqual(self.app.page, 'modal')
        self.app.erase.assert_not_called()
        self.app.render()
        # A bounced second contact during screen transition is ignored.
        self.app.handle_touch('down', (200, 230))
        self.app.handle_touch('up', (200, 230))
        self.app.erase.assert_not_called()
        self.app.ignore_touch_until = 0
        self.app.handle_touch('down', (200, 230))
        self.app.handle_touch('up', (200, 230))
        self.app.erase.assert_called_once()

    def test_settings_back_and_calibration_cancel_are_functional(self):
        def tap(point):
            self.app.ignore_touch_until = 0
            self.app.render()
            self.app.handle_touch('down', point)
            self.app.handle_touch('up', point, (1000, 1000))
        tap((400, 290))
        self.assertEqual(self.app.page, 'settings')
        tap((240, 284))
        self.assertEqual(self.app.page, 'main')
        tap((400, 290))
        tap((240, 82))
        self.assertEqual(self.app.page, 'calibrate')
        tap((240, 294))
        self.assertEqual(self.app.page, 'settings')
        self.assertEqual(self.app.calibration_points, [])
        self.assertFalse(self.app.config_path.exists())

    def test_language_replaces_close_and_survives_app_restart(self):
        self.app.set_page('settings')
        self.app.ignore_touch_until = 0
        self.app.render()
        self.assertNotIn('close', [button.key for button in self.app.buttons])
        self.app.handle_touch('down', (240, 154))
        self.app.handle_touch('up', (240, 154))
        self.assertEqual(self.app.page, 'language')
        self.assertTrue(self.app.running)
        self.app.ignore_touch_until = 0
        self.app.render()
        self.app.handle_touch('down', (240, 154))
        self.app.handle_touch('up', (240, 154))
        self.assertEqual(self.app.language, 'en')
        self.assertEqual(self.app.t('Réglages'), 'Settings')
        loaded = FramebufferApp(self.app.config_path)
        self.assertEqual(loaded.language, 'en')
        loaded.set_language('fr')
        self.assertEqual(FramebufferApp(self.app.config_path).language, 'fr')
        self.assertFalse(self.app.config_path.exists())

    def test_stopped_screen_has_no_stale_buttons(self):
        self.app.set_page('settings')
        self.app.render()
        self.app.request_stop()
        self.assertFalse(self.app.running)
        image = self.app.stopped_image()
        self.assertEqual(image.size, (480, 320))
        self.assertEqual(self.app.page, 'stopped')
        self.assertEqual(self.app.buttons, [])

    def test_calibration_saved_and_loaded_without_x(self):
        self.app.calibrate()
        raw = [(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)]
        for point in raw:
            self.app.calibration_tap(point)
        self.assertIsInstance(self.app.calibration, AffineCalibration)
        loaded = FramebufferApp(self.app.config_path)
        self.assertEqual(loaded.calibration.map(100, 100), (431, 271))

    def test_stop_during_format_waits_for_completion(self):
        self.app.busy, self.app.cancellable = True, False
        self.app.request_stop()
        self.assertTrue(self.app.running)
        self.assertFalse(self.app.cancel.is_set())
        self.app.emit('finished', 'Retrait autorisé')
        self.app.drain()
        self.assertFalse(self.app.running)


class AutostartTests(unittest.TestCase):
    def test_login_autostart_is_tty1_only_and_reversible(self):
        spec = importlib.util.spec_from_file_location('fb_install', Path(__file__).resolve().parents[1]/'install-framebuffer-autostart.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = 'export FRAMEBUFFER=/dev/fb1\n# startx désactivé\n'
        updated = module.update_profile(original, Path('/home/pi/sd2card/run-framebuffer.sh'))
        self.assertIn('"$(tty 2>/dev/null)" = /dev/tty1', updated)
        self.assertEqual(module.update_profile(updated, Path('/home/pi/sd2card/run-framebuffer.sh')), updated)
        restored = module.update_profile(updated, Path('/home/pi/sd2card/run-framebuffer.sh'), enable=False)
        self.assertEqual(restored.strip(), original.strip())
        with self.assertRaises(ValueError):
            module.update_profile('startx\n', Path('/fake'))


if __name__ == '__main__':
    unittest.main()
