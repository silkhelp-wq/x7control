"""USB side of the Sound Blaster X7: finding the device and resetting it.

The X7's USB HID control interface accepts SoundCore frames but never answers on Linux,
so everything useful happens over Bluetooth. What USB is still good for is a bus reset
(USBDEVFS_RESET) when PipeWire has lost the card. That needs write access to the USB
device node, which the shipped udev rule grants to the logged-in user.
"""
import fcntl
import glob
import os

VENDOR_ID = "041e"
PRODUCT_ID = "323a"
USBDEVFS_RESET = 0x5514


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def find_device():
    """Return (sysfs_path, /dev/bus/usb node) of the X7, or (None, None)."""
    for dev in sorted(glob.glob("/sys/bus/usb/devices/*")):
        if _read(dev + "/idVendor") != VENDOR_ID or _read(dev + "/idProduct") != PRODUCT_ID:
            continue
        try:
            node = "/dev/bus/usb/%03d/%03d" % (int(_read(dev + "/busnum")), int(_read(dev + "/devnum")))
        except (TypeError, ValueError):
            continue
        return dev, node
    return None, None


def serial():
    dev, _ = find_device()
    return _read(dev + "/serial") if dev else None


def reset():
    """Re-enumerate the X7. Returns a human-readable status line; raises OSError on failure."""
    dev, node = find_device()
    if not node:
        raise OSError("Sound Blaster X7 not found on USB")
    fd = os.open(node, os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET)
    finally:
        os.close(fd)
    return "USB reset sent to %s" % node
