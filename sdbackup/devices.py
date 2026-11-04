"""Conservative device discovery and UDisks operations (never run as root)."""
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Media:
    disk: str
    partition: str
    identity: tuple
    uuid: str
    label: str
    size: int
    filesystem: str
    sd: bool
    drive: str = ''

    @property
    def title(self):
        return f'{self.label} · {self.size / 10**9:.1f} Go · {self.partition}'


def load_sd_readers():
    """Explicit reader associations, never guesses based on disk size/removability."""
    try:
        readers = json.loads((Path(__file__).resolve().parents[1] / 'sd-readers.json').read_text())
        return readers if isinstance(readers, list) else []
    except (OSError, ValueError):
        return []


READER_KEYS = ('ID_VENDOR_ID', 'ID_MODEL_ID', 'ID_SERIAL_SHORT')


def usb_reader_counts(root=Path('/sys/bus/usb/devices')):
    """Count physical readers, including ones whose USB configuration failed."""
    counts = {}
    try:
        devices = list(root.iterdir())
    except OSError:
        return counts
    for device in devices:
        try:
            key = tuple((device / field).read_text().strip()
                        for field in ('idVendor', 'idProduct', 'serial'))
        except OSError:
            continue
        if all(key):
            counts[key] = counts.get(key, 0) + 1
    return counts


def known_sd_reader(properties, readers, connected=None):
    for reader in readers:
        if not isinstance(reader, dict):
            continue
        if not all(reader.get(key) and properties.get(key) == reader[key] for key in READER_KEYS):
            continue
        # An explicit port still disambiguates readers advertising identical IDs.
        if reader.get('ID_PATH_TAG') and properties.get('ID_PATH_TAG') == reader['ID_PATH_TAG']:
            return True
        # Follow a previously identified reader to another port only when unique.
        key = tuple(properties[field] for field in READER_KEYS)
        if connected is not None and connected.get(key) == 1:
            return True
    return False


def discover():
    raw = subprocess.check_output(['lsblk', '--tree', '-J', '-b', '-o',
        'PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,TRAN,RM,RO,MODEL,SERIAL,MAJ:MIN'], timeout=10)
    result = []
    readers = load_sd_readers()
    connected = usb_reader_counts()
    for disk in json.loads(raw)['blockdevices']:
        if disk['type'] != 'disk' or disk['tran'] not in ('usb', 'mmc') or disk['ro']:
            continue
        nodes = []
        def walk(n):
            nodes.append(n)
            for child in n.get('children', []):
                walk(child)
        walk(disk)
        # Exclude system disks, swap, encrypted/LVM and multi-partition media.
        if any(m and not m.startswith(('/media/', '/run/media/'))
               for n in nodes for m in n.get('mountpoints', [])):
            continue
        parts = [n for n in nodes if n['type'] == 'part']
        if len(parts) != 1 or len(nodes) != 2:
            continue
        part = parts[0]
        if not part['uuid'] or not part['fstype'] or part['ro']:
            continue
        name = os.path.basename(disk['path'])
        try:
            with open(f'/sys/class/block/{name}/diskseq') as f:
                sequence = f.read().strip()
            props = subprocess.check_output(['udevadm', 'info', '--query=property',
                                             '--path', f'/sys/class/block/{name}'], text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        properties = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
        sd = (properties.get('ID_DRIVE_FLASH_SD') == '1' or disk['tran'] == 'mmc'
              or known_sd_reader(properties, readers, connected))
        identity = (disk['path'], sequence, disk['serial'], disk['size'], part['uuid'])
        result.append(Media(disk['path'], part['path'], identity, part['uuid'],
                            part['label'] or disk['model'] or name,
                            int(part['size']), part['fstype'], sd))
    return result


def assert_present(media):
    if not any(m.identity == media.identity and m.partition == media.partition for m in discover()):
        raise SafetyError('Support retiré, remplacé ou configuration modifiée. Recommencez la copie.')


class Storage:
    def __init__(self):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()

    def interface(self, path, name):
        return self.dbus.Interface(self.bus.get_object('org.freedesktop.UDisks2', path),
                                   'org.freedesktop.UDisks2.' + name)

    def block(self, media):
        return '/org/freedesktop/UDisks2/block_devices/' + os.path.basename(media.partition)

    def props(self, path, name):
        return self.dbus.Interface(self.bus.get_object('org.freedesktop.UDisks2', path),
                                  'org.freedesktop.DBus.Properties').GetAll('org.freedesktop.UDisks2.' + name)

    def drive(self, media):
        return str(self.props(self.block(media), 'Block')['Drive'])

    def validate_pair(self, source, target):
        assert_present(source)
        assert_present(target)
        sd, usb = self.drive(source), self.drive(target)
        if source.disk == target.disk or sd == usb:
            raise SafetyError('Utilisez deux supports et deux lecteurs distincts.')
        for drive in (sd, usb):
            self.check_poweroff(drive)
        return sd, usb

    def check_poweroff(self, drive):
        properties = self.props(drive, 'Drive')
        if not properties['CanPowerOff']:
            raise SafetyError('Ce lecteur ne permet pas une éjection électrique sûre. Utilisez un autre lecteur USB.')
        sibling = str(properties.get('SiblingId', ''))
        if sibling:
            manager = self.dbus.Interface(self.bus.get_object('org.freedesktop.UDisks2',
                                          '/org/freedesktop/UDisks2'),
                                          'org.freedesktop.DBus.ObjectManager')
            for path, interfaces in manager.GetManagedObjects().items():
                other = interfaces.get('org.freedesktop.UDisks2.Drive')
                if str(path) != drive and other and str(other.get('SiblingId', '')) == sibling and int(other.get('Size', 0)) > 0:
                    raise SafetyError('Ce boîtier USB partage son alimentation avec un autre support. Utilisez deux lecteurs indépendants.')

    def mount(self, media, readonly=False):
        assert_present(media)
        fs = self.interface(self.block(media), 'Filesystem')
        # Force source read-only, including desktop automounts.
        mounts = self.props(self.block(media), 'Filesystem')['MountPoints']
        if mounts:
            fs.Unmount({}, timeout=120)
        options = {'options': 'ro' if readonly else 'rw'}
        return str(fs.Mount(options, timeout=120))

    def unmount(self, media):
        assert_present(media)
        if self.props(self.block(media), 'Filesystem')['MountPoints']:
            self.interface(self.block(media), 'Filesystem').Unmount({}, timeout=120)

    def format_sd(self, media):
        assert_present(media)
        self.interface(self.block(media), 'Block').Format('vfat', {
            'label': 'SD_CARD', 'update-partition-type': self.dbus.Boolean(True),
            'mkfs-args': self.dbus.Array(['-F', '32'], signature='s')}, timeout=600)

    def poweroff(self, drive):
        self.check_poweroff(drive)
        self.interface(drive, 'Drive').PowerOff({}, timeout=120)
