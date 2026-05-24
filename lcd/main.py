from machine import Pin, I2C
from esp8266_i2c_lcd import I2cLcd
import time

I2C_ADDR = 0x27
i2c = I2C(0, sda=Pin(8), scl=Pin(9), freq=100000)

found = i2c.scan()
print("I2C devices:", [hex(a) for a in found])
if I2C_ADDR not in found and found:
    I2C_ADDR = found[0]
    print("Falling back to first device:", hex(I2C_ADDR))

lcd = I2cLcd(i2c, I2C_ADDR, 2, 16)
lcd.clear()
lcd.putstr("ESP32-S3 + LCD")
lcd.move_to(0, 1)
lcd.putstr("Hello, Piyush!")

time.sleep(3)
for i in range(10, 0, -1):
    lcd.move_to(0, 1)
    lcd.putstr("Counting:  {:>2}   ".format(i))
    time.sleep(0.5)

lcd.clear()
lcd.putstr("Pi2.4 OK")
lcd.move_to(0, 1)
lcd.putstr("192.168.0.115")
