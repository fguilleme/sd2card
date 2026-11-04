"""Storage workflows shared by the X and direct-framebuffer interfaces."""
import logging
import os
from pathlib import Path
import threading
import time

from .copying import Backup, checkpoint, inventory, sync_filesystem
from .devices import SafetyError, Storage, assert_present


def copy_card(source, target, cancel, progress):
    storage = Storage()
    storage.validate_pair(source, target)
    src = storage.mount(source, readonly=True)
    if not os.statvfs(src).f_flag & os.ST_RDONLY:
        raise SafetyError('La carte ne peut pas être protégée en lecture seule.')
    dst = storage.mount(target)
    if os.stat(src).st_dev == os.stat(dst).st_dev:
        raise SafetyError('La source et la destination désignent le même support.')
    if target.filesystem == 'vfat':
        files, _ = inventory(Path(src), cancel)
        if any(size > 2**32 - 1 for size, _ in files.values()):
            raise SafetyError('Un fichier dépasse 4 Go : utilisez un disque USB en exFAT ou ext4.')
    src_device, dst_device = os.stat(src).st_dev, os.stat(dst).st_dev
    last_check = [0]
    def guard():
        if time.monotonic() - last_check[0] > 1:
            assert_present(source)
            assert_present(target)
            last_check[0] = time.monotonic()
        if os.stat(src).st_dev != src_device or os.stat(dst).st_dev != dst_device:
            raise SafetyError('Un support a été démonté pendant le travail.')
        if not os.statvfs(src).f_flag & os.ST_RDONLY:
            raise SafetyError('La protection en lecture seule de la carte a changé.')
    backup = Backup(src, dst, cancel, progress, guard)
    logging.info('Copie vérifiée : %s', backup.run())
    return backup


def erase_card(source, target, backup, cancel, emit):
    storage = Storage()
    assert_present(source)
    assert_present(target)
    sd_drive, usb_drive = storage.validate_pair(source, target)
    backup.verify()
    sync_filesystem(backup.destination)
    storage.unmount(target)
    storage.unmount(source)
    checkpoint(cancel)
    acknowledged = threading.Event()
    emit('formatting', acknowledged)
    if not acknowledged.wait(10):
        raise SafetyError('Interface indisponible : effacement refusé.')
    checkpoint(cancel)
    assert_present(target)
    storage.format_sd(source)
    logging.info('SD formatée en FAT32 : %s', source.partition)
    emit('phase', 'Éjection des deux supports…')
    storage.poweroff(sd_drive)
    storage.poweroff(usb_drive)
    return 'Carte effacée en FAT32. Les deux supports peuvent être retirés.'


def eject_media(media):
    storage = Storage()
    drives = []
    for item in media:
        assert_present(item)
        drive = storage.drive(item)
        storage.unmount(item)
        if drive not in drives:
            drives.append(drive)
    for drive in drives:
        storage.poweroff(drive)
    return 'Supports éjectés. La carte SD est conservée. Retrait autorisé.'
