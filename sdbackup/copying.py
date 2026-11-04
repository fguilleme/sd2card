"""Copy engine, independently testable without block devices."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
from datetime import datetime

from .devices import SafetyError

CHUNK = 1024 * 1024


class Cancelled(Exception):
    pass


def checkpoint(cancel):
    if cancel.is_set():
        raise Cancelled('Opération annulée. La carte SD est conservée.')


def inventory(root, cancel):
    files, directories = {}, []
    def visit(folder):
        checkpoint(cancel)
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                checkpoint(cancel)
                rel = str(Path(entry.path).relative_to(root))
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories.append(rel)
                    visit(entry.path)
                elif stat.S_ISREG(info.st_mode):
                    files[rel] = (info.st_size, info.st_mtime_ns)
                else:
                    raise SafetyError(f'Fichier spécial ou lien non pris en charge : {rel}')
    visit(root)
    return files, sorted(directories)


def open_read(path):
    return os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb')


def digest(path, cancel, advance):
    h = hashlib.sha256()
    with open_read(path) as f:
        if hasattr(os, 'posix_fadvise'):
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        while True:
            checkpoint(cancel)
            data = f.read(CHUNK)
            if not data:
                break
            h.update(data)
            advance(len(data))
    return h.hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_filesystem(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.syncfs(fd) != 0:
            raise OSError(ctypes.get_errno(), 'Échec de synchronisation du disque')
    finally:
        os.close(fd)


class Backup:
    def __init__(self, source, destination, cancel=None, progress=None, guard=None):
        self.source = Path(source)
        self.destination = Path(destination)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda *args: None)
        self.guard = guard or (lambda: None)
        self.folder = None
        self.manifest = None
        self.files = None
        self.directories = None
        self.total = 0
        self.done = 0

    def advance(self, size):
        self.guard()
        self.done += size
        self.progress(self.phase, self.done, self.total, self.current)

    def verify(self):
        if self.manifest is None:
            raise SafetyError('Aucune copie vérifiée disponible.')
        self.guard()
        files, directories = inventory(self.source, self.cancel)
        if files != self.files or directories != self.directories:
            raise SafetyError('Le contenu de la carte a changé.')
        dest_files, dest_dirs = inventory(self.folder / 'contenu', self.cancel)
        if {k: v[0] for k, v in dest_files.items()} != {k: v[0] for k, v in self.files.items()} or dest_dirs != self.directories:
            raise SafetyError('Le contenu de la sauvegarde a changé.')
        self.phase, self.done, self.total = 'Vérification SHA-256', 0, sum(v[0] for v in files.values()) * 2
        for rel, expected in self.manifest.items():
            self.current = rel
            self.guard()
            source_hash = digest(self.source / rel, self.cancel, self.advance)
            target_hash = digest(self.folder / 'contenu' / rel, self.cancel, self.advance)
            if source_hash != expected or target_hash != expected:
                raise SafetyError(f'Vérification échouée : {rel}. Carte conservée.')
        # Detect additions/removals during verification too.
        if inventory(self.source, self.cancel) != (self.files, self.directories):
            raise SafetyError('La carte a changé pendant la vérification.')
        self.guard()
        checkpoint(self.cancel)

    def run(self):
        self.guard()
        self.files, self.directories = inventory(self.source, self.cancel)
        if not self.files:
            raise SafetyError('La carte ne contient aucun fichier à sauvegarder.')
        self.total = sum(v[0] for v in self.files.values())
        needed = self.total + len(self.files) * 65536 + len(self.directories) * 65536 + 64 * 1024**2
        if shutil.disk_usage(self.destination).free < needed:
            raise SafetyError('Espace libre insuffisant sur le disque USB.')
        if os.statvfs(self.destination).f_flag & os.ST_RDONLY:
            raise SafetyError('Le disque USB est en lecture seule.')
        self.folder = self.destination / ('SD_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + '.incomplet')
        self.folder.mkdir(mode=0o700)
        data_root = self.folder / 'contenu'
        data_root.mkdir()
        for rel in self.directories:
            (data_root / rel).mkdir()
        self.phase, self.current, self.manifest = 'Copie', '', {}
        for rel in self.files:
            checkpoint(self.cancel)
            self.guard()
            self.current = rel
            h = hashlib.sha256()
            with open_read(self.source / rel) as src, open(data_root / rel, 'xb') as dst:
                while True:
                    checkpoint(self.cancel)
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    h.update(chunk)
                    self.advance(len(chunk))
                dst.flush()
                source_info = os.fstat(src.fileno())
                os.utime(dst.fileno(), ns=(source_info.st_atime_ns, source_info.st_mtime_ns))
                os.fsync(dst.fileno())
            self.manifest[rel] = h.hexdigest()
        for rel in reversed(self.directories):
            sync_directory(data_root / rel)
        sync_directory(data_root)
        sync_filesystem(self.destination)
        self.verify()
        report = {'version': 1, 'verified_at': datetime.now().astimezone().isoformat(),
                  'algorithm': 'SHA-256', 'files': self.manifest,
                  'directories': self.directories, 'bytes': sum(v[0] for v in self.files.values())}
        with open(self.folder / 'verification.json', 'x', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        sync_directory(self.folder)
        self.guard()
        checkpoint(self.cancel)
        final = self.folder.with_suffix('')
        self.folder.rename(final)
        self.folder = final
        sync_directory(self.destination)
        sync_filesystem(self.destination)
        return final
