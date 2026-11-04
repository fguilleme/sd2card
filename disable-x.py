#!/usr/bin/python3
"""Switch this Pi to console boot, without terminating the current X session."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PROFILE = Path('/home/pi/.bash_profile')
BACKUPS = Path('/var/backups/sdbackup-console')


def console_profile(text):
    lines = text.splitlines(keepends=True)
    result = []
    for line in lines:
        if re.match(r'^\s*(?:exec\s+)?(?:/usr/bin/)?startx(?:\s|$)', line):
            result.append('# Désactivé par sdbackup : ' + line.lstrip())
        else:
            result.append(line)
    return ''.join(result)


def replace_profile(path, data):
    info = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.console-', delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chown(temporary, info.st_uid, info.st_gid)
        temporary.chmod(info.st_mode & 0o777)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def systemctl(*args, check=True):
    return subprocess.run(['/usr/bin/systemctl', *args], check=check,
                          text=True, capture_output=True)


def restore(backup):
    backup = backup.resolve()
    if backup.parent != BACKUPS.resolve():
        raise RuntimeError('Chemin de sauvegarde inattendu.')
    original = (backup / 'bash_profile').read_bytes()
    state = json.loads((backup / 'state.json').read_text())
    replace_profile(PROFILE, original)
    systemctl('set-default', state['target'])
    if state['lightdm'] == 'enabled':
        systemctl('enable', 'lightdm.service')
    print('Configuration précédente restaurée. Elle prendra effet au prochain redémarrage.')


def disable():
    if PROFILE.is_symlink() or not PROFILE.is_file():
        raise RuntimeError('Le profil de connexion est absent ou est un lien symbolique : aucune modification.')
    original = PROFILE.read_bytes()
    updated = console_profile(original.decode('utf-8')).encode('utf-8')
    state = {'target': systemctl('get-default').stdout.strip(),
             'lightdm': systemctl('is-enabled', 'lightdm.service', check=False).stdout.strip()}
    if not state['target'].endswith('.target'):
        raise RuntimeError('Impossible de déterminer la cible de démarrage actuelle.')
    backup = BACKUPS / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(PROFILE, backup / 'bash_profile')
    (backup / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    try:
        replace_profile(PROFILE, updated)
        systemctl('set-default', 'multi-user.target')
        systemctl('disable', 'lightdm.service')
    except Exception:
        print('Échec du changement ; restauration des réglages précédents.')
        restore(backup)
        raise
    os.sync()
    print('Démarrage sans session X configuré.')
    print('La session actuelle reste ouverte. Redémarrez après toute copie en cours.')
    print('Après redémarrage : console sur le Pi, connexion automatique pi conservée.')
    print('L’application Tkinter actuelle ne démarre pas sans X ; la version framebuffer reste à réaliser.')
    print('Pour rétablir les réglages précédents :')
    print('sudo /usr/bin/python3 /home/pi/sd2card/disable-x.py --restore ' + str(backup))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Désactiver X au démarrage du Pi, sans arrêter la session actuelle')
    parser.add_argument('--restore', type=Path, help='Restaurer une sauvegarde créée par ce programme')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Lancez avec : sudo /usr/bin/python3 /home/pi/sd2card/disable-x.py')
    try:
        if args.restore:
            restore(args.restore)
        else:
            disable()
    except Exception as exc:
        raise SystemExit('Configuration non terminée : ' + str(exc))
