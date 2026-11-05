import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sdbackup.fb_app import FramebufferApp
from sdbackup.preferences import load_orientation, save_orientation, save_language


class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.app = FramebufferApp(self.path.with_name('touch.json'))

    def test_persistence_preserves_language(self):
        save_language(self.path, 'en')
        self.app.set_orientation(180)
        restarted = FramebufferApp(self.path.with_name('touch.json'))
        self.assertEqual(restarted.orientation, 180)
        self.assertEqual(restarted.language, 'en')
        save_language(self.path, 'fr')
        self.assertEqual(load_orientation(self.path), 180)

    def test_invalid_settings_default_to_normal(self):
        for value in ('[]', 'invalid', '{"orientation":45}', '{"orientation":true}'):
            self.path.write_text(value)
            self.assertEqual(load_orientation(self.path), 0)

    def test_rotated_pixels_and_contact_match(self):
        self.app.set_page('settings')
        normal = self.app.render()
        self.app.orientation = 180
        self.assertEqual(self.app.render().tobytes(), normal.rotate(180).tobytes())
        self.assertEqual(self.app.display_point((10, 20)), (469, 299))
        button = next(b for b in self.app.buttons if b.key == 'orientation')
        x = (button.rect[0]+button.rect[2])//2
        y = (button.rect[1]+button.rect[3])//2
        self.app.ignore_touch_until = 0
        self.app.handle_touch('down', (479-x, 319-y))
        self.app.handle_touch('up', (479-x, 319-y))
        self.assertEqual(self.app.page, 'orientation')

    def test_save_failure_keeps_current_orientation(self):
        with patch('sdbackup.fb_app.save_orientation', side_effect=OSError('disk full')):
            with self.assertLogs(level='ERROR'):
                self.app.set_orientation(180)
        self.assertEqual(self.app.orientation, 0)

    def test_busy_cannot_change_orientation(self):
        self.app.busy = True
        self.app.set_orientation(180)
        self.assertEqual(self.app.orientation, 0)
        self.assertFalse(self.path.exists())

    def test_all_rotations_render_and_map_contacts(self):
        for angle in (0, 90, 180, 270):
            self.app.set_orientation(angle)
            restarted = FramebufferApp(self.path.with_name('touch.json'))
            self.assertEqual(restarted.orientation, angle)
            for point in [(0, 0), (20, 50), (319, 319)]:
                self.assertEqual(self.app.display_point(self.app.physical_point(point)), point)
            for page in ('main', 'settings', 'orientation', 'language', 'choose', 'calibrate', 'stopped'):
                self.app.page = page
                self.assertEqual(self.app.render().size, (480, 320))
                w, h = self.app.screen_size
                for button in self.app.buttons:
                    x1, y1, x2, y2 = button.rect
                    self.assertTrue(0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h)
            self.app.set_page('settings')
            self.app.render()
            button = next(b for b in self.app.buttons if b.key == 'orientation')
            logical = ((button.rect[0]+button.rect[2])//2, (button.rect[1]+button.rect[3])//2)
            physical = self.app.physical_point(logical)
            self.app.ignore_touch_until = 0
            self.app.handle_touch('down', physical)
            self.app.handle_touch('up', physical)
            self.assertEqual(self.app.page, 'orientation')

    def test_portrait_calibration_targets_are_physical(self):
        from sdbackup.fbdev import AffineCalibration
        for angle in (90, 270):
            self.app.orientation = angle
            self.app.calibrate()
            targets = [self.app.physical_point(p) for p in self.app.calibration_targets]
            with patch('sdbackup.fb_app.AffineCalibration.fit', wraps=AffineCalibration.fit) as fit:
                for point in targets:
                    self.app.calibration_tap(point)
                self.assertEqual(fit.call_args.args[1], targets)
