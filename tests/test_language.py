import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from sdbackup.fb_app import FramebufferApp, Screen
from sdbackup.i18n import translate
from sdbackup.preferences import load_language, save_language


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'settings.json'

    def test_default_and_corrupt_preferences_use_french(self):
        self.assertEqual(load_language(self.path), 'fr')
        for data in ('not json', '[]', '{"language":"unknown"}', '{"language":[]}'):
            self.path.write_text(data)
            self.assertEqual(load_language(self.path), 'fr')

    def test_saving_preserves_other_preferences_and_reloads_language(self):
        self.path.write_text('{"future_setting":42}')
        save_language(self.path, 'en')
        self.assertEqual(load_language(self.path), 'en')
        self.assertEqual(json.loads(self.path.read_text())['future_setting'], 42)
        with self.assertRaises(ValueError):
            save_language(self.path, 'invalid')
        self.assertEqual(load_language(self.path), 'en')

    def test_failed_write_keeps_previous_language(self):
        save_language(self.path, 'fr')
        with patch('sdbackup.preferences.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                save_language(self.path, 'en')
        self.assertEqual(load_language(self.path), 'fr')
        self.assertEqual(list(self.path.parent.glob('.settings-*')), [])

    def test_ui_does_not_claim_save_succeeded_on_error(self):
        app = FramebufferApp(self.path.with_name('touch.json'))
        with patch('sdbackup.fb_app.save_language', side_effect=OSError('write failed')):
            with self.assertLogs(level='ERROR'):
                app.set_language('en')
        self.assertEqual(app.language, 'fr')
        self.assertEqual(app.page, 'modal')

    def test_dynamic_messages_and_confirmation_are_translated(self):
        self.assertEqual(translate('Copie · 42 %', 'en'), 'Copying · 42 %')
        self.assertEqual(translate('Calibration 3/5 : touchez la cible', 'en'), 'Calibration 3/5: tap the target')
        original = 'MA CARTE • 31.3 Go\n/dev/sda1\nCopie vérifiée sur MON DISQUE.\nLe contenu de la SD sera effacé en FAT32.'
        english = translate(original, 'en')
        self.assertIn('MA CARTE', english)
        self.assertIn('MON DISQUE', english)
        self.assertIn('/dev/sda1', english)
        self.assertIn('31.3 GB', english)
        self.assertIn('erased as FAT32', english)
        self.assertEqual(translate('Vérification échouée : été.jpg. Carte conservée.', 'en'),
                         'Verification failed: été.jpg. SD card kept intact.')

    def test_file_names_are_not_translated(self):
        app = FramebufferApp(self.path.with_name('touch.json'))
        app.set_language('en')
        app.emit('progress', ('Copie', 5, 10, 'Annuler'))
        app.drain()
        app.render()
        self.assertTrue(app.detail_is_data)
        screen = Screen(app)
        with patch.object(app, 't', wraps=app.t) as translate_call:
            screen.text('Annuler', (0, 0, 100, 40), localize=False)
            translate_call.assert_not_called()
        self.assertEqual(app.detail, 'Annuler')


if __name__ == '__main__':
    unittest.main()
