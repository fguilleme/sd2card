#!/usr/bin/python3
"""Privileged local installer. Called by the graphical launcher using pkexec."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def cmdline(text):
    lines = text.strip().splitlines()
    if len(lines) != 1 or not any(token.startswith('root=') for token in lines[0].split()):
        raise RuntimeError('Ligne de démarrage inattendue : installation refusée.')
    tokens = lines[0].split()
    for option in ('quiet', 'splash', 'logo.nologo', 'plymouth.ignore-serial-consoles'):
        if option not in tokens:
            tokens.append(option)
    return ' '.join(tokens) + '\n'


def firmware_config(text):
    marker = '# Sauvegarde SD : masquer le test couleur du firmware'
    if marker not in text:
        text = text.rstrip() + '\n\n[all]\n' + marker + '\ndisable_splash=1\n'
    return text


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.sdbackup-', delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def install(project, root=Path('/'), run=subprocess.run):
    assets = project / 'assets/boot'
    for name in ('background.png', 'sdbackup.plymouth', 'sdbackup.script'):
        if not (assets / name).is_file():
            raise RuntimeError('Image ou thème manquant : ' + name)
    boot = root / 'boot/firmware'
    config = boot / 'config.txt'
    command = boot / 'cmdline.txt'
    new_config = firmware_config(config.read_text())
    new_command = cmdline(command.read_text())
    # Validate everything before making any change to the system.
    theme = root / 'usr/share/plymouth/themes/sdbackup'
    wallpaper = root / 'usr/local/share/sdbackup/background.png'
    files = [config, command, root / 'etc/plymouth/plymouthd.conf', wallpaper]
    files += [theme / name for name in ('background.png', 'sdbackup.plymouth', 'sdbackup.script')]
    files += list(boot.glob('initramfs*')) + list((root / 'boot').glob('initrd.img-*'))
    backup = root / 'var/backups/sdbackup-appearance' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    existing = []
    missing = []
    for path in files:
        if path.exists():
            target = backup / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            existing.append(path)
        else:
            missing.append(path)
    atomic_write(backup / 'README.txt', ('Sauvegarde avant changement du visuel.\n'
                 'Les fichiers sont classés suivant leur chemin système.\n').encode())
    print('Sauvegarde des réglages : ' + str(backup), flush=True)
    try:
        for name in ('background.png', 'sdbackup.plymouth', 'sdbackup.script'):
            atomic_write(theme / name, (assets / name).read_bytes())
        atomic_write(wallpaper, (assets / 'background.png').read_bytes())
        atomic_write(config, new_config.encode())
        atomic_write(command, new_command.encode())
        run(['/usr/sbin/plymouth-set-default-theme', 'sdbackup'], check=True)
        print('Reconstruction des images de démarrage ; patientez…', flush=True)
        run(['/usr/sbin/update-initramfs', '-u', '-k', 'all'], check=True)
        os.sync()
    except Exception:
        print('Échec : restauration des fichiers précédents…', flush=True)
        for path in existing:
            shutil.copy2(backup / path.relative_to(root), path)
        for path in missing:
            path.unlink(missing_ok=True)
        os.sync()
        raise
    print('Installation terminée. Redémarrage à effectuer après toute copie en cours.', flush=True)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Ce programme doit être lancé par le raccourci graphique avec pkexec.')
    try:
        install(Path(__file__).resolve().parent)
    except Exception as exc:
        print('Installation échouée : ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
