import machine
import sdcard
import uos
import ota

# Assign chip select (CS) pin (and start it high)
cs = machine.Pin(22, machine.Pin.OUT)

# Intialize SPI peripheral (start with 1 MHz)
spi = machine.SPI(id=0,
                  baudrate=10000000,
                  polarity=0,
                  phase=0,
                  bits=8,
                  firstbit=machine.SPI.MSB,
                  sck=machine.Pin(5),
                  mosi=machine.Pin(18),
                  miso=machine.Pin(19))

# Initialize SD card
sd = sdcard.SDCard(spi, cs,baudrate=5_000_000)

# Mount filesystem
uos.mount(sd, '/sd')

# check for code changes on webserver
ota.check_and_update()
