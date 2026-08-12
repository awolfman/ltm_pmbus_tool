"""Bus factory -- unified bus creation.

    bus_num   0.. 99  -> smbus2.SMBus        /dev/i2c-N
    bus_num 100..199  -> CH341Bus            USB CH341 #N
    bus_num 200..299  -> FtdiBus             USB FTDI  #N
"""

import smbus2

CH341_OFFSET = 100
FTDI_OFFSET  = 200


def create_bus(bus_num):
    if bus_num >= FTDI_OFFSET:
        from core.ftdi_i2c import FtdiBus
        return FtdiBus(bus_num - FTDI_OFFSET)
    if bus_num >= CH341_OFFSET:
        from core.ch341_i2c import CH341Bus
        return CH341Bus(bus_num - CH341_OFFSET)
    return smbus2.SMBus(bus_num)
