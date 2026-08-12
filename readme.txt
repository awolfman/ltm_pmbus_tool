
CH341 Linux permissions:
  sudo python main.py
  -- or add udev rule --
  # CH341 has two USB PIDs depending on mode:
#   0x5512 = I2C mode
#   0x5523 = Serial mode (also does I2C via stream commands)
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5512", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5523", MODE="0666"' | \
    sudo tee /etc/udev/rules.d/99-ch341.rules
  sudo udevadm control --reload-rules

Bus number        Adapter              Library      Install
  0..99           /dev/i2c-N           smbus2       pip install smbus2
100..199          CH341T/A  (0x5512)   pyusb        pip install pyusb
200..299          FT232H    (0x6014)   pyftdi       pip install pyftdi
                  FT2232H   (0x6010)
                  FT4232H   (0x6011)

FT232H          LTM4673/4677
 AD0  (SCL) ----+---- SCL
 AD1  (SDA) ----+---- SDA
 AD2  (SDA) ----+
 GND  ----------+----- GND
                 |
              [2.2k]  pull-up to 3.3V on SCL and SDA

Если Linux не отдаёт FT232H

Ядро автоматически захватывает FTDI-чипы модулем ftdi_sio:
sudo rmmod ftdi_sio usbserial
python main.py

Или постоянное правило:
echo 'ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6014", RUN+="/bin/sh -c echo $kernel > /sys/bus/usb/drivers/ftdi_sio/unbind"' | \
  sudo tee /etc/udev/rules.d/99-ftdi-unbind.rules
sudo udevadm control --reload-rules
