"""480 × 320 touch appliance, rendered directly to Linux /dev/fb1."""
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import select
import signal
import threading
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .copying import Cancelled
from .devices import discover
from .i18n import LANGUAGES, translate
from .preferences import load_language, save_language
from .fbdev import AffineCalibration, Calibration, Console, Framebuffer, Touchscreen
from .operations import copy_card, eject_media, erase_card

BG, PANEL, FG, GREEN, AMBER, RED = '#10202d', '#1d3445', '#f3f7fa', '#79dfb2', '#f5c978', '#f4a5a5'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


@dataclass
class Button:
    key: str
    rect: tuple
    action: object
    enabled: bool = True

    def contains(self, point):
        x, y = point
        a, b, c, d = self.rect
        return a <= x < c and b <= y < d


class Screen:
    def __init__(self, app):
        self.app = app
        self.image = Image.new('RGB', (480, 320), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.buttons = []

    def text(self, text, rect, size=17, color=FG, bold=False, center=True, maxlines=2, localize=True):
        text = self.app.t(text) if localize else str(text)
        font = ImageFont.truetype(BOLD if bold else FONT, size)
        width = rect[2]-rect[0]-12
        lines = []
        for paragraph in str(text).splitlines():
            line = ''
            for word in paragraph.split():
                proposed = (line + ' ' + word).strip()
                if self.draw.textlength(proposed, font=font) > width and line:
                    lines.append(line)
                    line = word
                else:
                    line = proposed
            lines.append(line)
        if len(lines) > maxlines:
            lines = lines[:maxlines]
            lines[-1] += '…'
        for i, line in enumerate(lines):
            while self.draw.textlength(line, font=font) > width and len(line) > 1:
                line = line[:-2] + '…'
            x = (rect[0]+rect[2]-self.draw.textlength(line, font=font))/2 if center else rect[0]+6
            y = (rect[1]+rect[3]-len(lines)*(size+4))/2 + i*(size+4)
            self.draw.text((x, y), line, fill=color, font=font)

    def button(self, key, text, rect, action, enabled=True, primary=False, danger=False, localize=True):
        color = GREEN if primary else PANEL
        if danger:
            color = RED
        if self.app.pressed and self.app.pressed.key == key and enabled:
            color = '#a3ebd0' if primary else '#36576e'
        self.draw.rounded_rectangle(rect, radius=8, fill=color)
        self.text(text, rect, size=17, bold=True,
                  color=(BG if primary or danger else FG) if enabled else '#8596a3', localize=localize)
        self.buttons.append(Button(key, rect, action, enabled))


class FramebufferApp:
    def __init__(self, config_path=None, settings_path=None):
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.media = []
        self.source = self.target = None
        self.pair = self.backup = None
        self.state = 'waiting'
        self.busy = self.scanning = False
        self.cancellable = False
        self.status = 'En attente des supports'
        self.detail = 'Insérez la carte SD et le disque USB.'
        self.detail_is_data = False
        self.percent = 0
        self.page = 'main'
        self.modal = None
        self.choice_role = None
        self.choice_offset = 0
        self.buttons = []
        self.pressed = None
        self.dirty = True
        self.running = True
        self.stop_requested = False
        self.last_scan = self.last_progress = 0
        self.ignore_touch_until = 0
        self.config_path = config_path or Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'sdbackup/touch.json'
        self.settings_path = Path(settings_path) if settings_path else self.config_path.with_name('settings.json')
        self.language = load_language(self.settings_path)
        self.calibration = Calibration()
        self.format_ack = None
        if self.config_path.exists():
            try:
                self.calibration = AffineCalibration(json.loads(self.config_path.read_text())['coefficients'])
            except (OSError, ValueError, KeyError, TypeError):
                logging.warning('Calibration enregistrée invalide : reprise du réglage initial')
        self.calibration_points = []
        self.calibration_targets = [(48, 48), (431, 48), (431, 271), (48, 271), (240, 160)]
        self.calibration_listener = None

    def t(self, text):
        return translate(text, self.language)

    def set_language(self, language):
        if self.busy or language not in LANGUAGES:
            return
        try:
            save_language(self.settings_path, language)
        except OSError as exc:
            logging.exception('Impossible d’enregistrer la langue')
            self.ask('Enregistrement impossible', str(exc), lambda: None, 'Compris', single=True)
            return
        self.language = language
        self.set_page('language')

    def emit(self, kind, data):
        self.events.put((kind, data))

    def launch(self, work, cancellable=True):
        if self.busy:
            return
        self.busy = True
        self.cancellable = cancellable
        self.cancel.clear()
        self.percent = 0
        self.page = 'main'
        self.pressed = None
        self.dirty = True
        def run():
            try:
                kind, result = work()
                self.emit(kind, result)
            except Cancelled as exc:
                self.emit('cancelled', str(exc))
            except Exception as exc:
                logging.exception('Opération échouée')
                self.emit('error', str(exc))
        threading.Thread(target=run, daemon=True).start()

    def progress(self, phase, done, total, filename):
        now = time.monotonic()
        if now - self.last_progress >= .25 or done == total:
            self.last_progress = now
            self.emit('progress', (phase, done, total, filename))

    def start_copy(self):
        if self.busy or not self.source or not self.target or self.source.disk == self.target.disk:
            return
        source, target = self.source, self.target
        self.pair = (source, target)
        self.backup = None
        self.status = 'Préparation des supports…'
        self.detail = 'Ne retirez aucun support.'
        self.detail_is_data = False
        self.launch(lambda: ('verified', copy_card(source, target, self.cancel, self.progress)))

    def confirm_erase(self):
        if self.busy or self.state != 'verified' or not self.backup or not self.pair:
            return
        source, target = self.pair
        self.ask('Effacer cette carte SD ?',
                 f'{source.label} • {source.size/10**9:.1f} Go\n{source.partition}\nCopie vérifiée sur {target.label}.\nLe contenu de la SD sera effacé en FAT32.',
                 self.erase, 'Effacer la SD', danger=True)

    def erase(self):
        if not self.backup or self.state != 'verified' or self.busy:
            return
        # Capture all objects before the worker starts; selections are then locked.
        source, target = self.pair
        backup = self.backup
        self.status = 'Dernière vérification…'
        self.detail = 'Ne retirez aucun support.'
        self.detail_is_data = False
        self.launch(lambda: ('finished', erase_card(source, target, backup, self.cancel, self.emit)))

    def eject(self):
        media = [m for m in (self.source, self.target) if m]
        if not media or self.busy:
            return
        self.status = 'Éjection en cours…'
        self.detail = 'Ne retirez pas les supports avant la fin.'
        self.detail_is_data = False
        self.launch(lambda: ('finished', eject_media(media)), cancellable=False)

    def cancel_work(self):
        if self.busy and self.cancellable:
            self.cancel.set()
            self.cancellable = False
            self.status = 'Annulation en cours…'
            self.dirty = True

    def stopped_image(self, message='Application fermée'):
        self.page = 'stopped'
        self.status = message
        self.pressed = None
        return self.render()

    def request_stop(self):
        if self.busy:
            self.stop_requested = True
            self.cancel_work()
        else:
            self.running = False

    def scan(self):
        if self.busy or self.scanning or self.page == 'calibrate':
            return
        self.last_scan = time.monotonic()
        self.scanning = True
        def run():
            try:
                self.emit('media', discover())
            except Exception as exc:
                self.emit('scan_error', str(exc))
        threading.Thread(target=run, daemon=True).start()

    def receive_media(self, media):
        self.scanning = False
        if self.busy:
            return
        previous = self.media
        self.media = media
        current = {m.identity for m in media}
        removed = False
        for role in ('source', 'target'):
            old = getattr(self, role)
            if old and old.identity not in current:
                setattr(self, role, None)
                removed = True
        if removed:
            self.backup = self.pair = None
            self.state = 'waiting'
            self.percent = 0
            self.page = 'main'
            self.pressed = None
        sds = [m for m in media if m.sd]
        if not self.source and len(sds) == 1 and (not self.target or self.target.disk != sds[0].disk):
            self.source = sds[0]
        targets = [m for m in media if self.source and m.disk != self.source.disk]
        if not self.target and len(targets) == 1:
            self.target = targets[0]
        if self.state == 'finished' and media:
            self.state = 'waiting'
        if self.state == 'waiting':
            self.detail_is_data = False
            ready = self.source and self.target
            self.status = 'Prêt à sauvegarder' if ready else 'En attente des supports'
            if ready:
                self.detail = 'Vérifiez les supports puis lancez la copie.'
            elif media and not self.source:
                self.detail = 'Touchez CARTE SD pour choisir votre carte.'
            elif self.source:
                self.detail = 'Insérez le disque USB de sauvegarde.'
            else:
                self.detail = 'Insérez la carte SD et le disque USB.'
        if previous != media or removed:
            self.dirty = True

    def drain(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == 'media':
                self.receive_media(value)
                continue
            self.dirty = True
            if kind == 'scan_error':
                self.scanning = False
                self.media = []
                self.source = self.target = self.backup = self.pair = None
                self.state = 'error'
                self.status = 'Détection indisponible'
                self.detail = str(value)
                self.detail_is_data = False
            elif kind == 'progress':
                phase, done, total, filename = value
                self.percent = min(100, 100*done/total) if total else 100
                self.status = f'{phase} · {self.percent:.0f} %'
                self.detail = filename
                self.detail_is_data = True
            elif kind == 'formatting':
                self.cancellable = False
                self.status = 'Formatage FAT32 en cours…'
                self.detail = 'Ne retirez aucun support.'
                self.detail_is_data = False
                self.pressed = None
                self.format_ack = value
            elif kind == 'phase':
                self.status = value
            elif kind == 'verified':
                self.busy = self.cancellable = False
                self.state = 'verified'
                self.backup = value
                self.percent = 100
                self.status = 'Copie terminée et vérifiée'
                self.detail = value.folder.name
                self.detail_is_data = True
            elif kind == 'finished':
                self.busy = self.cancellable = False
                self.state = 'finished'
                self.source = self.target = self.pair = self.backup = None
                self.status = 'Terminé — retrait autorisé'
                self.detail = value
                self.detail_is_data = False
                self.percent = 100
            elif kind in ('error', 'cancelled'):
                self.busy = self.cancellable = False
                self.state = 'error'
                self.backup = self.pair = None
                self.status = 'Opération interrompue' if kind == 'cancelled' else 'Opération échouée'
                self.detail = str(value)
                self.detail_is_data = False
                if kind == 'error':
                    self.ask('Opération interrompue', str(value), lambda: None, 'Compris', single=True)
            if not self.busy and self.stop_requested:
                self.running = False

    def acknowledge_render(self):
        if self.format_ack is not None:
            acknowledgement, self.format_ack = self.format_ack, None
            acknowledgement.set()

    def ask(self, title, text, action, label, danger=False, single=False):
        self.modal = (title, text, action, label, danger, single)
        self.set_page('modal')

    def set_page(self, page):
        self.page = page
        self.pressed = None
        self.ignore_touch_until = time.monotonic() + .35
        self.dirty = True

    def choose(self, role):
        if self.busy or self.state == 'verified':
            return
        self.choice_role, self.choice_offset = role, 0
        self.set_page('choose')

    def select(self, media):
        if media not in self.media or self.busy or self.state == 'verified':
            self.set_page('main')
            return
        setattr(self, self.choice_role, media)
        self.backup = self.pair = None
        self.state = 'waiting'
        self.receive_media(list(self.media))
        self.set_page('main')

    def calibrate(self):
        if self.busy:
            return
        self.calibration_points = []
        self.set_page('calibrate')

    def calibration_tap(self, raw):
        if raw[0] is None or raw[1] is None:
            return
        self.calibration_points.append(raw)
        if len(self.calibration_points) == len(self.calibration_targets):
            try:
                calibration = AffineCalibration.fit(self.calibration_points, self.calibration_targets)
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.config_path.with_suffix('.tmp')
                with open(temp, 'w') as handle:
                    json.dump({'coefficients': calibration.coefficients}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp.replace(self.config_path)
                self.calibration = calibration
                if self.calibration_listener:
                    self.calibration_listener(calibration)
                self.ask('Calibration enregistrée', 'Le tactile est prêt. Testez les boutons au doigt.', lambda: None, 'Continuer', single=True)
            except Exception as exc:
                self.ask('Calibration à refaire', str(exc), self.calibrate, 'Recommencer')
        self.dirty = True

    def handle_touch(self, kind, point, raw=None):
        if kind == 'cancel':
            self.pressed = None
            self.dirty = True
            return
        if time.monotonic() < self.ignore_touch_until:
            return
        if self.page == 'calibrate':
            if kind == 'down':
                self.pressed = next((b for b in self.buttons if b.enabled and b.contains(point)), None)
                self.dirty = True
            elif kind == 'up':
                pressed, self.pressed = self.pressed, None
                self.dirty = True
                if pressed:
                    if pressed.contains(point):
                        pressed.action()
                elif raw is not None:
                    self.calibration_tap(raw)
            return
        if kind == 'down':
            self.pressed = next((b for b in self.buttons if b.enabled and b.contains(point)), None)
            self.dirty = True
        elif kind == 'up':
            pressed, self.pressed = self.pressed, None
            self.dirty = True
            if pressed and pressed.contains(point):
                current = next((b for b in self.buttons if b.key == pressed.key and b.enabled), None)
                if current:
                    current.action()

    def render(self):
        screen = Screen(self)
        if self.page == 'stopped':
            screen.text(self.status, (8, 56, 472, 112), 24, bold=True)
            screen.text('Pour relancer depuis le terminal :', (8, 132, 472, 170), 17)
            screen.text('./run-framebuffer.sh', (8, 180, 472, 222), 20, bold=True, color=GREEN)
            screen.text('Dans le dossier /home/pi/sd2card', (8, 244, 472, 282), 15)
        elif self.page == 'calibrate':
            index = len(self.calibration_points)
            screen.text(f'Calibration {index+1}/5 : touchez la cible', (0, 0, 480, 32), 17, bold=True, maxlines=1)
            x, y = self.calibration_targets[index]
            screen.draw.ellipse((x-23, y-23, x+23, y+23), outline=GREEN, width=3)
            screen.draw.line((x-14, y, x+14, y), fill=FG, width=2)
            screen.draw.line((x, y-14, x, y+14), fill=FG, width=2)
            screen.button('cancel-calibration', 'Annuler', (165, 270, 315, 318), lambda: self.set_page('settings'))
        elif self.page == 'modal':
            title, text, action, label, danger, single = self.modal
            screen.text(title, (8, 6, 472, 43), 20, bold=True, maxlines=1)
            screen.text(text, (12, 46, 468, 193), 16, maxlines=6)
            def accept():
                self.set_page('main')
                action()
            screen.button('accept', label, (8, 204, 472, 256), accept, primary=True, danger=danger)
            if not single:
                screen.button('back', 'Retour', (8, 264, 472, 314), lambda: self.set_page('main'))
        elif self.page == 'settings':
            screen.text('Réglages', (8, 3, 472, 35), 21, bold=True)
            screen.button('calibrate', 'Calibrer le tactile', (8, 52, 472, 112), self.calibrate)
            screen.button('language', self.t('Langue') + (': ' if self.language == 'en' else ' : ') + LANGUAGES[self.language],
                          (8, 124, 472, 184), lambda: self.set_page('language'), localize=False)
            screen.button('back', 'Retour', (8, 254, 472, 314), lambda: self.set_page('main'))
        elif self.page == 'language':
            screen.text('Langue', (8, 3, 472, 35), 21, bold=True)
            screen.button('language-fr', 'Français', (8, 52, 472, 112),
                          lambda: self.set_language('fr'), primary=self.language == 'fr', localize=False)
            screen.button('language-en', 'English', (8, 124, 472, 184),
                          lambda: self.set_language('en'), primary=self.language == 'en', localize=False)
            screen.button('back', 'Retour', (8, 254, 472, 314), lambda: self.set_page('settings'))
        elif self.page == 'choose':
            screen.text('Choisir la carte SD' if self.choice_role == 'source' else 'Choisir le disque USB', (0, 0, 480, 35), 20, bold=True)
            other = self.target if self.choice_role == 'source' else self.source
            choices = [m for m in self.media if not other or m.disk != other.disk]
            self.choice_offset = min(self.choice_offset, max(0, len(choices)-1))
            for index, media in enumerate(choices[self.choice_offset:self.choice_offset+3]):
                y = 42 + index*66
                units = 'GB' if self.language == 'en' else 'Go'
                label = f'{media.label} · {media.size/10**9:.1f} {units} · {media.partition}'
                screen.button('media:'+repr(media.identity), label, (8, y, 472, y+58), lambda m=media: self.select(m), localize=False)
            if not choices:
                screen.text('Aucun support disponible', (8, 70, 472, 150), 18)
            if len(choices) > 3:
                def more():
                    self.choice_offset = (self.choice_offset+3) % len(choices)
                    self.dirty = True
                screen.button('more', 'Suite', (8, 254, 230, 314), more)
                screen.button('back', 'Retour', (242, 254, 472, 314), lambda: self.set_page('main'))
            else:
                screen.button('back', 'Retour', (8, 254, 472, 314), lambda: self.set_page('main'))
        else:
            screen.text('SAUVEGARDE SD', (8, 0, 472, 30), 21, bold=True, maxlines=1)
            for role, media, title, rect in (
                ('source', self.source, 'CARTE SD', (8, 36, 234, 128)),
                ('target', self.target, 'DISQUE USB', (246, 36, 472, 128))):
                screen.draw.rounded_rectangle(rect, radius=8, fill=PANEL)
                screen.text(title, (rect[0], 39, rect[2], 62), 16, bold=True, color=GREEN if media else AMBER, maxlines=1)
                units = 'GB' if self.language == 'en' else 'Go'
                text = f'{media.label}\n{media.size/10**9:.1f} {units} · {media.partition}' if media else ('Toucher pour choisir' if self.media else ('Non détectée' if role == 'source' else 'Non détecté'))
                screen.text(text, (rect[0], 66, rect[2], 124), 14, maxlines=3, localize=media is None)
                screen.buttons.append(Button(role, rect, lambda r=role: self.choose(r), not self.busy and self.state != 'verified'))
            screen.text(self.status, (4, 134, 476, 159), 17, bold=True, maxlines=1)
            screen.text(self.detail, (4, 160, 476, 184), 13, maxlines=1, localize=not self.detail_is_data)
            screen.draw.rounded_rectangle((8, 189, 472, 199), radius=4, fill=PANEL)
            if self.percent:
                screen.draw.rounded_rectangle((8, 189, 8+max(4, round(464*self.percent/100)), 199), radius=4, fill=GREEN)
            ready = bool(self.source and self.target and self.source.disk != self.target.disk)
            screen.button('erase' if self.state == 'verified' else 'copy',
                          'Effacer la SD et éjecter' if self.state == 'verified' else 'Lancer la copie',
                          (8, 208, 472, 260), self.confirm_erase if self.state == 'verified' else self.start_copy,
                          ready and not self.busy, primary=True)
            screen.button('cancel' if self.busy else 'eject', 'Annuler' if self.busy else 'Éjecter sans effacer',
                          (8, 268, 326, 316), self.cancel_work if self.busy else self.eject,
                          self.cancellable if self.busy else bool(self.source or self.target))
            screen.button('settings', 'Réglages', (338, 268, 472, 316), lambda: self.set_page('settings'), not self.busy)
        self.buttons = screen.buttons
        self.dirty = False
        return screen.image


def main():
    parser = argparse.ArgumentParser(description='Interface tactile directe, sans X')
    parser.add_argument('--framebuffer', default='/dev/fb1')
    parser.add_argument('--touch', help='Périphérique evdev ; ADS7846 détecté automatiquement par défaut')
    parser.add_argument('--calibrate', action='store_true')
    parser.add_argument('--preview', type=Path, help='Enregistrer un aperçu PNG sans accéder au matériel')
    args = parser.parse_args()
    app = FramebufferApp()
    if args.calibrate or (not args.preview and not app.config_path.exists()):
        app.calibrate()
    if args.preview:
        app.render().save(args.preview)
        return
    if os.geteuid() == 0:
        raise SystemExit('Lancez depuis la console avec le compte pi, sans sudo.')
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))) / 'sdbackup'
    state.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(state/'app.log', maxBytes=2_000_000, backupCount=3)
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    lock = open(state/'app.lock', 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Sauvegarde SD est déjà ouverte.')
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + '\n')
    lock.flush()
    fb = console = touch = None
    try:
        # Validate devices before taking over the console.
        fb = Framebuffer(args.framebuffer)
        touch = Touchscreen(args.touch, app.calibration)
        console = Console(fb)
        app.calibration_listener = lambda calibration: setattr(touch.events, 'calibration', calibration)
        def request_stop(signum, frame):
            app.request_stop()
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        last_render = 0
        while app.running:
            app.drain()
            now = time.monotonic()
            if now - app.last_scan >= 1.5:
                app.scan()
            if app.dirty and now-last_render >= .15:
                fb.show(app.render())
                app.acknowledge_render()
                last_render = now
            inputs = [touch.fd] + ([console.fd] if console.fd is not None else [])
            readable, _, _ = select.select(inputs, [], [], .04)
            if touch.fd in readable:
                try:
                    for kind, point, raw in touch.read():
                        app.handle_touch(kind, point, raw)
                except (OSError, RuntimeError) as exc:
                    logging.exception('Erreur du tactile')
                    # Stop accepting contacts, cancel before any destructive boundary.
                    touch.close()
                    app.request_stop()
                    while app.busy:
                        app.drain()
                        time.sleep(.05)
                    raise RuntimeError('Tactile indisponible : ' + str(exc)) from exc
            if console.fd is not None and console.fd in readable:
                keys = os.read(console.fd, 32)
                if b'q' in keys or b'\x1b' in keys:
                    app.request_stop()
        logging.info('Retour à la console')
    finally:
        if app.busy:
            app.cancel.set()
            while app.busy:
                app.drain()
                time.sleep(.05)
        if fb is not None:
            try:
                # A dedicated SPI display retains its last image after the process exits.
                # Never leave a screen of apparently active buttons behind.
                fb.show(app.stopped_image())
            except Exception:
                logging.exception('Impossible d’afficher le message d’arrêt')
        for device in (touch, console, fb):
            if device:
                device.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        logging.error('Arrêt de l’interface : %s', exc)
        raise SystemExit(str(exc))
