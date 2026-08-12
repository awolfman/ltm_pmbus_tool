# core/bus_scanner.py
"""I2C bus scanning -- /dev/i2c + CH341 + FTDI."""

import functools
from .ch341_i2c import find_ch341_devices
from .pmbus_device import PMBusDevice
from .bus_factory import create_bus

def find_buses():
    devices = find_ch341_devices()
    return list(range(len(devices)))

def bus_label(bus_num):
    devices = find_ch341_devices()
    if bus_num < len(devices):
        dev = devices[bus_num]
        return f"CH341 0x{dev.idProduct:04X} #{bus_num}"
    return f"CH341 #{bus_num}"

def scan_bus(bus_num):
    devices = []
    print(f"[scan_bus] scanning CH341 0x5512 #{bus_num} ...")

    try:
        bus = create_bus(bus_num)
        if hasattr(bus, 'reset_bus'):
            bus.reset_bus()

        for addr in range(0x0C, 0x80):
            if addr in [0x5A, 0x5B]:
                continue

            if not bus.detect(addr):
                continue

            try:
                special_id = bus.read_word_data(addr, 0xE7)
            except Exception:
                continue

            if special_id in (0xFFFF, 0x0000):
                continue

            print(f"[DEBUG] found potential device at 0x{addr:02X}, id=0x{special_id:04X}")

            dev = PMBusDevice(bus_num, addr, special_id)

            if dev.identify():
                devices.append(dev)
                print(f"[scan_bus]  +  @ 0x{addr:02X}  pages={dev.num_pages}  id=0x{special_id:04X}  {dev.name}")
            else:
                print(f"[scan_bus]  -  @ 0x{addr:02X} не удалось идентифицировать устройство")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[scan] CH341 error: {e}")
        return []

    print(f"[scan_bus] found {len(devices)} device(s)")
    return devices

if __name__ == "__main__":
    import sys
    bus_num = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(f"Scanning CH341 #{bus_num}...")
    found = scan_bus(bus_num)
    if found:
        print("\nFound devices:")
        for dev in found:
            print(f"  Address: 0x{dev.address:02X}")
            print(f"    Name: {dev.name}")
            print(f"    ID: 0x{dev.special_id:04X}")
            print(f"    Pages: {dev.num_pages}")
            print(f"    Revision: {dev.revision}")
            print()
    else:
        print("No devices found.")
