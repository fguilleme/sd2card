#!/usr/bin/python3
"""Install native framebuffer autostart in pi's local-console login profile."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import shlex
import tempfile

START = '# BEGIN SDBACKUP FRAMEBUFFER'
END = '# END SDBACKUP FRAMEBUFFER'


def update_profile(text, launcher, enable=True):
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('Bloc de démarrage inattendu : vérifiez le profil manuellement.')
    text = re.sub(r'(?m)^' + re.escape(START) + r'\n.*?^' + re.escape(END) + r'\n?', '', text, flags=re.S)
    if enable:
        if any(re.match(r'^\s*(?:exec\s+)?(?:/usr/bin/)?startx(?:\s|$)', line) for line in text.splitlines()):
            raise ValueError('startx est encore actif. Désactivez X avant cette installation.')
        text = text.rstrip() + '\n\n' + START + '\n'
        text += 'if [ "$(tty 2>/dev/null)" = /dev/tty1 ]; then\n'
        text += '    ' + shlex.quote(str(launcher)) + '\n'
        text += 'fi\n' + END + '\n'
    return text


def install(profile, launcher, enable=True):
    if profile.is_symlink():
        raise ValueError('Le profil est un lien symbolique : installation refusée.')
    original = profile.read_text() if profile.exists() else ''
    updated = update_profile(original, launcher, enable)
    backup = profile.with_name(profile.name + '.before-framebuffer-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    if profile.exists():
        shutil.copy2(profile, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=profile.parent,
                                         prefix='.sdbackup-profile-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(profile.stat().st_mode & 0o777 if profile.exists() else 0o644)
        temporary.replace(profile)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Démarrage automatique de Sauvegarde SD sans X')
    parser.add_argument('--disable', action='store_true', help='Revenir à la console sans lancer l’application')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('Exécutez avec le compte pi, sans sudo.')
    try:
        launcher = Path(__file__).resolve().parent / 'run-framebuffer.sh'
        saved = install(Path.home()/'.bash_profile', launcher, enable=not args.disable)
        print('Démarrage framebuffer ' + ('désactivé.' if args.disable else 'activé sur la console tty1.'))
        print('Sauvegarde du profil : ' + str(saved))
        print('La modification prendra effet à la prochaine connexion console ou au prochain redémarrage.')
        print('Pour lancer maintenant, sur la console du Pi : ' + str(launcher))
    except Exception as exc:
        raise SystemExit('Installation non terminée : ' + str(exc))
