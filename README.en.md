# SD Backup — Raspberry Pi

[Documentation française](README.md)

Touchscreen application that copies an SD card to a USB drive, verifies the files, then offers to format the card as FAT32 and eject both devices. The interface uses the Linux framebuffer directly, **without X or Wayland**.

## Hardware and system in use

- Raspberry Pi 4 running Raspberry Pi OS / Debian 13 in a console session.
- 480 × 320 SPI ILI9486 display: `/dev/fb1`, RGB565.
- ADS7846 resistive touchscreen, detected under `/dev/input/event*`.
- An external USB SD reader and a separate USB backup drive.

The Pi's boot microSD is excluded from selection. Display and touchscreen drivers must already be configured; this repository does not install them.

## Installation and launch

These commands match the `pi` account and `/home/pi/sd2card` directory used on this machine.

Dependencies:

```sh
sudo apt install python3 python3-pil python3-dbus udisks2 dosfstools udev util-linux
```

The application account needs access to the framebuffer and input devices. Here, `pi` already belongs to the `video` and `input` groups. On another installation:

```sh
sudo usermod -aG video,input pi
```

Log in again after changing groups. Start the application as `pi`, **without sudo**:

```sh
cd /home/pi/sd2card
./run-framebuffer.sh
```

The first launch offers five-point touchscreen calibration. To repeat it immediately:

```sh
./run-framebuffer.sh --calibrate
```

The application checks framebuffer geometry, colour fields and memory bounds. It supports the ILI9486 driver's RGB565 mode, which the kernel marks as `nonstd=1`, and respects the driver's line stride and display offsets.

## Automatic startup without X

To disable X startup on this Pi:

```sh
sudo python3 /home/pi/sd2card/disable-x.py
```

The script backs up the settings, comments out the direct `startx` call in `pi`'s login profile, selects `multi-user.target` and disables LightDM. It preserves the existing console autologin. It does not terminate the current session or reboot the machine. A restoration command is printed when it finishes.

To start the application during automatic login on tty1:

```sh
python3 /home/pi/sd2card/install-framebuffer-autostart.py
```

Run this second script **without sudo**. It backs up `~/.bash_profile` and adds a block restricted to `/dev/tty1`. SSH connections retain their normal shell. Console autologin for `pi` must already be configured.

The change takes effect at the next console login or boot. To remove application autostart:

```sh
python3 /home/pi/sd2card/install-framebuffer-autostart.py --disable
```

On this Pi, the main console uses fb0 and the dedicated touchscreen uses fb1. Direct display output can therefore also be launched over SSH. UDisks operations still require appropriate authorization: **autostart in the active local console session is the normal operating mode**. A shared lock prevents multiple application instances.

## Daily use

1. Insert the SD card in its external reader and connect the USB backup drive.
2. Check the two large media indicators. If a generic reader is unknown, tap **SD CARD** or **USB DRIVE** to select the correct device.
3. Tap **Start backup**. The SD card is mounted read-only. Progress shows copying followed by SHA-256 verification.
4. After **Backup complete and verified**, select **Erase SD and eject both** if the card should be reused. A confirmation identifies the card to be erased.
5. Wait for **Done — safe to remove** before unplugging the media.

**Eject without erasing** keeps the SD contents. **Cancel** interrupts copying or verification; cancellation may wait for an ongoing system operation. Once formatting starts, cancellation is disabled and formatting must finish.

## Settings and languages

- **Calibrate touchscreen**: repeat the five touches. **Cancel** keeps the previous calibration.
- **Language**: choose **Français** or **English**. Changes apply immediately and are saved automatically for future launches, including after a reboot.
- **Back**: return to the previous screen.

Preferences are stored in:

```text
~/.config/sdbackup/settings.json    # language
~/.config/sdbackup/touch.json       # calibration
```

Device labels and filenames remain unchanged. Escape or `q` in the application's terminal requests an exit; during an operation, exit waits for cancellation or formatting completion. The display then shows an explicit stopped message rather than inactive buttons.

## Backup layout

```text
SD_2026-11-04_18-30-00_123456/
    contenu/             # files, hidden files and empty directories
    verification.json    # inventory and SHA-256 hashes
```

Each run creates a new dated directory. Interrupted or failed copies retain the `.incomplet` suffix. Previous backups are not overwritten, and incomplete directories are not resumed automatically.

This is a file backup, not a sector image: boot sectors and Unix permissions are not backed up. File modification times are preserved within the destination filesystem's capabilities.

## Checks before erasing

- System disks, swap, encrypted/LVM volumes, multi-partition media and mounts outside `/media` or `/run/media` are excluded.
- Each device must have one partition with a recognized filesystem, a UUID and write access. Blank media are not partitioned automatically.
- Device identity, kernel device generation, mount locations and source read-only protection are checked.
- Free space is checked; files exceeding 4 GiB are refused when the destination uses FAT.
- Symbolic links and special files are refused.
- Files and the destination filesystem are synchronized, then every source and destination file is reread and compared using SHA-256.
- A full second verification, destination synchronization and successful unmount of both media are required before formatting. Failure at these stages blocks erasing.
- Only the SD partition is formatted as FAT32. UDisks then powers off the devices. Enclosures sharing power with other attached media are refused.

Quick formatting prepares a card for reuse; it is not confidential data erasure. Power loss during formatting may leave the card needing another format. Software verification and synchronization cannot guarantee a hardware device's internal cache behaviour.

## Reader recognition and USB troubleshooting

`sd-readers.json` lists readers identified on this installation. Known readers are matched by vendor, product and serial number, independently of the port when that identity is unique. If multiple readers advertise identical identities, the saved port is used to disambiguate them. A device's role is never guessed from capacity alone.

The generic `349c:0418` reader has intermittently reported `can't set config #1, error -75` during connection. In that state Linux does not create a block device, so the application cannot read the card. The Genesys `05e3:0747` reader was detected successfully and registered as an SD reader. Its long-term reliability still needs testing.

Inspect visible USB devices, disks and kernel messages with:

```sh
lsusb -t
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,TRAN
journalctl -k -b --no-pager -n 60
```

The rotating application log is `~/.local/state/sdbackup/app.log`. `XDG_CONFIG_HOME` and `XDG_STATE_HOME` can change the configuration and log locations.

## Boot image

The Plymouth theme is in `assets/boot/`. To install the boot image from a console:

```sh
sudo python3 /home/pi/sd2card/install-appearance-root.py
```

The script backs up affected files under `/var/backups/sdbackup-appearance/<date>/`, installs the theme and rebuilds the initramfs images. A failure triggers restoration of the backed-up files. It does not reboot automatically. The SPI display may stay black until the kernel initializes it.

The vector source and PNG are included. Regeneration requires `python3-cairo`:

```sh
python3 assets/make_background.py
```

## Tests and validation status

```sh
python3 -m unittest discover -s tests -v
```

Copying and formatting tests use temporary directories and simulated devices. They do not format real media.

Hardware checks completed: RGB565 framebuffer output, exclusive ADS7846 input, calibration, settings navigation, languages and persistence after application restart, console autostart, Polkit authorization and display of a real SD card.

**A complete copy / verification / format / eject cycle using test media still needs validation.**

This version contains the framebuffer application and its setup tools. Earlier X interface experiments and USB timing experiments are outside this version.
