"""I2C bus scanning -- /dev/i2c + CH341 + FTDI."""

import os, sys
from core.bus_factory import create_bus, CH341_OFFSET, FTDI_OFFSET
from core.pmbus_device import PMBusDevice


def is_sim():
    return '--sim' in sys.argv or '--demo' in sys.argv


def find_buses():
    if is_sim():
        return [1]

    buses = []

    # ---- Linux /dev/i2c-N ----
    for i in range(20):
        if os.path.exists(f"/dev/i2c-{i}"):
            buses.append(i)

    # ---- CH341 USB ----
    try:
        from core.ch341_i2c import find_ch341_devices
        for idx in range(len(find_ch341_devices())):
            buses.append(CH341_OFFSET + idx)
    except ImportError:
        pass
    except Exception as e:
        print(f"[scan] CH341 error: {e}")

    # ---- FTDI USB (FT232H / FT2232H / FT4232H) ----
    try:
        from core.ftdi_i2c import find_ftdi_devices
        for idx in range(len(find_ftdi_devices())):
            buses.append(FTDI_OFFSET + idx)
    except ImportError:
        pass
    except Exception as e:
        print(f"[scan] FTDI error: {e}")

    return buses


def bus_label(bus_num):
    if bus_num >= FTDI_OFFSET:
        idx = bus_num - FTDI_OFFSET
        try:
            from core.ftdi_i2c import find_ftdi_devices, I2C_PIDS
            devs = find_ftdi_devices()
            if idx < len(devs):
                d = devs[idx]
                chip = I2C_PIDS.get(d.pid, '?')
                sn = getattr(d, 'sn', '') or ''
                return f"FTDI FT{chip.upper()} {sn}".strip()
        except Exception:
            pass
        return f"FTDI #{idx}"
    if bus_num >= CH341_OFFSET:
        idx = bus_num - CH341_OFFSET
        try:
            from core.ch341_i2c import find_ch341_devices
            devs = find_ch341_devices()
            if idx < len(devs):
                d = devs[idx]
                pid_str = f"0x{d.idProduct:04X}"
                return f"CH341 {pid_str} #{idx}"
        except Exception:
            pass
        return f"CH341 #{idx}"
    return f"/dev/i2c-{bus_num}"


def scan_bus(bus_num):
    devices = []
    bl = bus_label(bus_num)
    print(f"[scan_bus] scanning {bl} ...")
    with create_bus(bus_num) as bus:
        for addr in range(0x08, 0x78):
            try:
                bus.read_byte(addr)
                dev = PMBusDevice(bus_num, addr)
                if dev.identify():
                    devices.append(dev)
                    print(f"[scan_bus]  + {dev.name} "
                          f"@ 0x{addr:02X}  "
                          f"pages={dev.num_pages}  "
                          f"id=0x{dev.special_id:04X}")
            except Exception:
                continue
    print(f"[scan_bus] found {len(devices)} device(s)")
    return devices
