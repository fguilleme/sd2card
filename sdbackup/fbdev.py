"""Linux fbdev output and evdev input. No display server or GUI toolkit."""
from array import array
import ctypes
import errno
import fcntl
import logging
import mmap
import os
from pathlib import Path
import struct
import termios
import tty


class FixedInfo(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 16), ('smem_start', ctypes.c_ulong),
                ('smem_len', ctypes.c_uint32), ('type', ctypes.c_uint32),
                ('type_aux', ctypes.c_uint32), ('visual', ctypes.c_uint32),
                ('xpanstep', ctypes.c_uint16), ('ypanstep', ctypes.c_uint16),
                ('ywrapstep', ctypes.c_uint16), ('line_length', ctypes.c_uint32),
                ('mmio_start', ctypes.c_ulong), ('mmio_len', ctypes.c_uint32),
                ('accel', ctypes.c_uint32), ('capabilities', ctypes.c_uint16),
                ('reserved', ctypes.c_uint16 * 2)]


def encode_pixels(image, bits, red, green, blue):
    """Pack true-colour pixels in the native byte order requested by fbdev."""
    if bits not in (16, 32):
        raise ValueError('Framebuffer : seuls les modes truecolor 16 et 32 bits sont pris en charge.')
    fields = (red, green, blue)
    if any(length <= 0 or length > 8 or offset < 0 or offset + length > bits
           for offset, length in fields):
        raise ValueError('Format de couleur framebuffer non pris en charge.')
    if any(set(range(a, a+n)) & set(range(b, b+m))
           for i, (a, n) in enumerate(fields) for b, m in fields[i+1:]):
        raise ValueError('Champs de couleur framebuffer superposés.')
    lookup = [[((value >> (8-length)) << offset) for value in range(256)] for offset, length in fields]
    r, g, b = lookup
    pixels = array('H' if bits == 16 else 'I',
                   (r[x] | g[y] | b[z] for x, y, z in image.convert('RGB').getdata()))
    return pixels.tobytes()


class Framebuffer:
    def __init__(self, path='/dev/fb1'):
        self.fd = os.open(path, os.O_RDWR)
        self.path = path
        self.memory = None
        try:
            fixed_buffer = bytearray(ctypes.sizeof(FixedInfo))
            fcntl.ioctl(self.fd, 0x4602, fixed_buffer, True)
            self.fixed = FixedInfo.from_buffer_copy(fixed_buffer)
            variable = bytearray(160)
            fcntl.ioctl(self.fd, 0x4600, variable, True)
            values = struct.unpack('=40I', variable)
            self.width, self.height = values[:2]
            self.xoffset, self.yoffset = values[4:6]
            self.bits = values[6]
            self.red, self.green, self.blue = values[8:10], values[11:13], values[14:16]
            # fbtft explicitly marks its RGB565 framebuffer nonstd=1.
            # Permit that known ILI9486 mode without accepting arbitrary non-standard layouts.
            ili9486_rgb565 = (
                self.fixed.id == b'fb_ili9486' and values[20] == 1 and self.bits == 16
                and self.red == (11, 5) and self.green == (5, 6) and self.blue == (0, 5)
                and values[18] == 0
            )
            description = (f'{self.fixed.id.decode(errors="replace")} {self.width}x{self.height} '
                           f'{self.bits} bits, type={self.fixed.type}, visual={self.fixed.visual}, '
                           f'grayscale={values[7]}, nonstd={values[20]}, '
                           f'RGB={self.red}/{self.green}/{self.blue}')
            logging.info('Framebuffer détecté : %s', description)
            if (self.fixed.type != 0 or self.fixed.visual != 2 or self.bits not in (16, 32)
                    or values[7] or (values[20] and not ili9486_rgb565)):
                raise ValueError('Format framebuffer non pris en charge : ' + description)
            if any(values[i] for i in (10, 13, 16)):
                raise ValueError('Ordre de bits framebuffer non pris en charge.')
            self.stride = self.fixed.line_length
            self.rowbytes = self.width * (self.bits // 8)
            self.start = self.yoffset * self.stride + self.xoffset * (self.bits // 8)
            end = self.start + (self.height - 1) * self.stride + self.rowbytes
            if self.width < 480 or self.height < 320 or self.xoffset * (self.bits // 8) + self.rowbytes > self.stride or end > self.fixed.smem_len:
                raise ValueError('Framebuffer trop petit ou géométrie invalide (minimum 480 × 320).')
            self.memory = mmap.mmap(self.fd, self.fixed.smem_len, mmap.MAP_SHARED,
                                    mmap.PROT_READ | mmap.PROT_WRITE)
        except Exception:
            self.close()
            raise

    def separate_from_console(self):
        """A dedicated SPI display can be used without acquiring an SSH terminal."""
        try:
            name = Path(self.path).name
            if not name.startswith('fb') or not name[2:].isdigit():
                return False
            active = Path('/sys/class/tty/tty0/active').read_text().strip()
            if not active.startswith('tty') or not active[3:].isdigit():
                return False
            mapping = bytearray(struct.pack('=II', int(active[3:]), 0))
            fcntl.ioctl(self.fd, 0x460F, mapping, True)  # FBIOGET_CON2FBMAP
            return struct.unpack('=II', mapping)[1] != int(name[2:])
        except (OSError, ValueError):
            return False

    def show(self, image):
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        data = encode_pixels(image, self.bits, self.red, self.green, self.blue)
        if self.stride == self.rowbytes and self.xoffset == 0:
            self.memory[self.start:self.start + len(data)] = data
        else:
            for row in range(self.height):
                start = self.start + row * self.stride
                self.memory[start:start+self.rowbytes] = data[row*self.rowbytes:(row+1)*self.rowbytes]

    def close(self):
        if self.memory is not None:
            self.memory.close()
            self.memory = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Console:
    """Keep console text off the framebuffer; always restore the original mode."""
    def __init__(self, framebuffer=None):
        self.fd = None
        self.settings = self.mode = None
        separate = framebuffer is not None and framebuffer.separate_from_console()
        try:
            self.fd = os.open('/dev/tty', os.O_RDWR | os.O_NONBLOCK)
            self.settings = termios.tcgetattr(self.fd)
            if not separate:
                mode = array('i', [0])
                fcntl.ioctl(self.fd, 0x4B3B, mode, True)
                self.mode = mode[0]
            tty.setcbreak(self.fd)
            if self.mode is not None:
                fcntl.ioctl(self.fd, 0x4B3A, 1)
            if separate:
                logging.info('Écran dédié distinct de la console : accès direct sans changement du mode VT')
        except OSError as exc:
            self.close()
            if separate and exc.errno in (errno.ENOTTY, errno.ENXIO, errno.ENOENT):
                logging.info('Écran dédié sans terminal de contrôle ; pilotage tactile uniquement')
                return
            if exc.errno in (errno.ENOTTY, errno.ENXIO):
                raise RuntimeError(
                    'Ce terminal n’est pas une console Linux locale (SSH ou terminal virtuel). '
                    'Le framebuffer est disponible, mais le contrôle de la console nécessite tty1. '
                    'Lancez l’application sur la console du Pi, ou activez son démarrage automatique '
                    'avec python3 /home/pi/sd2card/install-framebuffer-autostart.py puis redémarrez.'
                ) from exc
            raise
        except Exception:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            try:
                if self.mode is not None:
                    fcntl.ioctl(self.fd, 0x4B3A, self.mode)
                if self.settings is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.settings)
            finally:
                os.close(self.fd)
                self.fd = None


class Calibration:
    def __init__(self, xmin=3936, xmax=227, ymin=268, ymax=3880, swap=True):
        if xmin == xmax or ymin == ymax:
            raise ValueError('Calibration tactile invalide.')
        self.bounds = xmin, xmax, ymin, ymax
        self.swap = swap

    def map(self, raw_x, raw_y, width=480, height=320):
        xmin, xmax, ymin, ymax = self.bounds
        x = (raw_x - xmin) / (xmax - xmin)
        y = (raw_y - ymin) / (ymax - ymin)
        if self.swap:
            x, y = y, x
        return round(max(0, min(1, x)) * (width-1)), round(max(0, min(1, y)) * (height-1))


class TouchEvents:
    """Frame events at SYN_REPORT; discard contacts after an event overflow."""
    def __init__(self, calibration=None):
        self.calibration = calibration or Calibration()
        self.x = self.y = None
        self.down = self.reported = False
        self.dropped = False
        self.wait_release = False

    def feed(self, kind, code, value):
        if kind == 0 and code == 3:  # SYN_DROPPED
            self.down = self.reported = False
            self.dropped = self.wait_release = True
            return ('cancel', None)
        if self.dropped:
            if kind == 0 and code == 0:
                self.dropped = False
            return None
        if kind == 1 and code == 330:  # BTN_TOUCH
            self.down = bool(value)
            if not self.down:
                self.wait_release = False
        elif kind == 3 and code == 0:
            self.x = value
        elif kind == 3 and code == 1:
            self.y = value
        elif kind == 0 and code == 0 and not self.wait_release:
            if self.x is None or self.y is None:
                return None
            point = self.calibration.map(self.x, self.y)
            if self.down:
                event = 'move' if self.reported else 'down'
                self.reported = True
                return event, point
            if self.reported:
                self.reported = False
                return 'up', point
        return None


class Touchscreen:
    EVENT = struct.Struct('@llHHi')

    def __init__(self, path=None, calibration=None):
        if path is None:
            matches = [p for p in Path('/sys/class/input').glob('event*')
                       if 'ADS7846' in (p / 'device/name').read_text()]
            if len(matches) != 1:
                raise RuntimeError('Contrôleur ADS7846 absent ou ambigu.')
            path = '/dev/input/' + matches[0].name
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.events = TouchEvents(calibration)
        self.buffer = b''
        try:
            fcntl.ioctl(self.fd, 0x40044590, 1)  # EVIOCGRAB
        except Exception:
            self.close()
            raise

    def read(self):
        data = os.read(self.fd, self.EVENT.size * 64)
        if not data:
            raise RuntimeError('Le contrôleur tactile a été déconnecté.')
        self.buffer += data
        result = []
        while len(self.buffer) >= self.EVENT.size:
            _, _, kind, code, value = self.EVENT.unpack_from(self.buffer)
            self.buffer = self.buffer[self.EVENT.size:]
            item = self.events.feed(kind, code, value)
            if item:
                result.append((*item, (self.events.x, self.events.y)))
        return result

    def close(self):
        if self.fd is not None:
            os.close(self.fd)  # closes the exclusive grab too
            self.fd = None


class AffineCalibration:
    """A raw-evdev to logical-screen transformation, independent of X."""
    def __init__(self, coefficients):
        import math
        if len(coefficients) != 6 or not all(math.isfinite(v) for v in coefficients):
            raise ValueError('Matrice de calibration invalide.')
        a, b, _, d, e, _ = coefficients
        if abs(a*e - b*d) < 1e-9:
            raise ValueError('Calibration tactile dégénérée.')
        self.coefficients = list(coefficients)

    def map(self, x, y, width=480, height=320):
        a, b, c, d, e, f = self.coefficients
        return round(max(0, min(width-1, a*x+b*y+c))), round(max(0, min(height-1, d*x+e*y+f)))

    @classmethod
    def fit(cls, raw, screen):
        if len(raw) < 3 or len(raw) != len(screen):
            raise ValueError('Trois points de calibration sont nécessaires.')
        matrix = [[raw[i][0], raw[i][1], 1, screen[i][0], screen[i][1]] for i in range(3)]
        for col in range(3):
            pivot = max(range(col, 3), key=lambda r: abs(matrix[r][col]))
            matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
            value = matrix[col][col]
            if abs(value) < 1e-9:
                raise ValueError('Appuis trop proches : recommencez la calibration.')
            matrix[col] = [v/value for v in matrix[col]]
            for row in range(3):
                if row != col:
                    factor = matrix[row][col]
                    matrix[row] = [x-factor*y for x, y in zip(matrix[row], matrix[col])]
        coefficients = [matrix[i][3] for i in range(3)] + [matrix[i][4] for i in range(3)]
        result = cls(coefficients)
        for point, expected in zip(raw[3:], screen[3:]):
            a, b, c, d, e, f = result.coefficients
            actual = a*point[0]+b*point[1]+c, d*point[0]+e*point[1]+f
            if max(abs(actual[i] - expected[i]) for i in (0, 1)) > 20:
                raise ValueError('Les appuis ne concordent pas : recommencez la calibration.')
        return result
