import network, time
from machine import Pin, I2C
import ssd1306

SSID = "Pi2.4"
PASS = "81481187"

# Light the OLED immediately so the screen is alive within ~100ms of power-on,
# even before main.py is reached. Catches any I2C/wiring failures right away.
_HAVE_OLED = False
try:
    _i2c = I2C(0, sda=Pin(10), scl=Pin(11), freq=400000)
    _oled = ssd1306.SSD1306_I2C(128, 64, _i2c)
    _oled.fill(0)
    _oled.text("BOOT", 0, 0)
    _oled.hline(0, 10, 128, 1)
    _oled.text("WiFi:" + SSID, 0, 16)
    _oled.text("connecting...", 0, 32)
    _oled.show()
    _HAVE_OLED = True
except Exception:
    pass

def _oled_line(text, y):
    if not _HAVE_OLED:
        return
    try:
        _oled.fill_rect(0, y, 128, 8, 0)
        _oled.text(text[:16], 0, y)
        _oled.show()
    except Exception:
        pass

sta = network.WLAN(network.STA_IF)
sta.active(True)
time.sleep(1)

try:
    sta.scan()
except Exception:
    pass

for attempt in range(1, 6):
    if sta.isconnected():
        break
    print("Connect attempt", attempt, "to", SSID)
    _oled_line("attempt {}/5".format(attempt), 32)
    try:
        sta.disconnect()
    except Exception:
        pass
    time.sleep(0.5)
    sta.connect(SSID, PASS)
    for _ in range(40):
        if sta.isconnected():
            break
        time.sleep(0.5)
    print("  -> status", sta.status(), "connected", sta.isconnected())

if sta.isconnected():
    ip, mask, gw, dns = sta.ifconfig()
    print("WiFi OK. IP={}  GW={}  DNS={}  RSSI={}".format(ip, gw, dns, sta.status("rssi")))
    _oled_line("OK " + ip, 32)
    _oled_line("loading main.py", 48)
else:
    print("WiFi FAILED after retries. last status =", sta.status())
    _oled_line("WiFi FAILED", 32)
    _oled_line("retry in main.py", 48)
