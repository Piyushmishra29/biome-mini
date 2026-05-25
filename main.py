from machine import Pin, I2C, ADC, WDT
import ssd1306, sys, time, network, json

try:
    import urequests as requests
except ImportError:
    import requests

PI_URL = "http://192.168.0.114:8000/api/mq"
MSG_URL = "http://192.168.0.114:8000/api/oled_msg"
# Hosted on the Pi now (always-on, static LAN IP). Same hostname as PI_URL above.
MAC_PLANT_URL = "http://192.168.0.114:3020/api/ingest"
PLANT_ID = "pothos-1"
READ_PERIOD_MS = 200
POST_EVERY_N = 25
PLANT_POST_OFFSET = 12  # offset tick so Mac POST doesn't collide with Pi POST
MSG_FETCH_EVERY_N = 10
HIST_LEN = 128
BASELINE_WINDOW = 240

# Sensor module's AO is currently compressed at ~1V regardless of moisture.
# Flip to False once the wiring/module is fixed -- pipeline is unchanged.
USE_SYNTHETIC_SOIL = False

i2c = I2C(0, sda=Pin(10), scl=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
adc = ADC(Pin(4), atten=ADC.ATTN_11DB)
soil_adc = ADC(Pin(5), atten=ADC.ATTN_11DB)
sta = network.WLAN(network.STA_IF)
# Hardware watchdog: if the main loop hangs for >15s the chip auto-resets.
# Long enough to absorb a slow POST (4s timeout) but short enough that the
# OLED is never frozen for more than ~15s before boot.py redraws splash.
wdt = WDT(timeout=15000)

# Soil calibration: dry-in-air vs fully wet.
# Tune by checking raw value with probe dry and with probe in water.
SOIL_DRY_RAW = 65000
SOIL_WET_RAW = 22000

history = []
baseline_buf = []
soil_pct_last = 50.0

def read_mq():
    samples = [adc.read_u16() for _ in range(8)]
    raw = sum(samples) // len(samples)
    v = raw * 3.3 / 65535
    return raw, v * 1000.0, v

def oled_reinit():
    # Full SSD1306 init sequence -- only path that recovers a deeply-stuck
    # panel (e.g. after a power dip or controller reset). Brief flicker.
    global oled
    try:
        oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    except Exception:
        pass

def oled_kick():
    # Defensive per-frame wake: cheap poweron+contrast (3 I2C bytes).
    # Handles the common case where display has gone into power-save.
    # On exception, escalates to a full re-init.
    try:
        oled.poweron()
        oled.contrast(255)
    except Exception:
        oled_reinit()

def read_soil():
    samples = [soil_adc.read_u16() for _ in range(16)]
    raw = sum(samples) // len(samples)
    pct = (SOIL_DRY_RAW - raw) / (SOIL_DRY_RAW - SOIL_WET_RAW) * 100.0
    return raw, max(0.0, min(100.0, pct))

def read_soil_synth():
    # 4-min sine wave 0-100% so the dashboard pipeline is testable
    # while the hardware module is being fixed.
    import math
    t = time.ticks_ms() / 1000.0
    pct = 50.0 + 45.0 * math.sin(2 * math.pi * t / 240.0)
    raw = int(SOIL_DRY_RAW - (pct / 100.0) * (SOIL_DRY_RAW - SOIL_WET_RAW))
    return raw, pct

def post_plant(raw, pct):
    payload = json.dumps({"plant_id": PLANT_ID, "soil_raw": raw, "soil_pct": round(pct, 1)})
    try:
        r = requests.post(MAC_PLANT_URL, data=payload,
                          headers={"Content-Type": "application/json"}, timeout=1.5)
        try:
            return r.status_code
        finally:
            r.close()
    except Exception:
        return None

MAX_V_AT_3V3 = 1.5

def category(v, base):
    delta = v - base
    if delta < 0.02:  return "CLEAN"
    if delta < 0.08:  return "MILD"
    if delta < 0.20:  return "ELEVATED"
    if delta < 0.40:  return "SMOKE!"
    return "HEAVY"

def post(raw, mv, idx):
    payload = json.dumps({"voc_raw": raw, "voc_mv": round(mv, 1), "voc_index": round(idx, 1)})
    r = requests.post(PI_URL, data=payload, headers={"Content-Type": "application/json"}, timeout=4)
    try:
        return r.status_code
    finally:
        r.close()

def fetch_msg():
    try:
        r = requests.get(MSG_URL, timeout=3)
        try:
            data = r.json()
        finally:
            r.close()
        return data.get("message"), data.get("ttl", 0.0)
    except Exception:
        return None, 0.0

def wrap_text(text, max_chars):
    out = []
    for para in text.split("\n"):
        words = para.split()
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if len(cand) <= max_chars:
                cur = cand
            else:
                if cur: out.append(cur)
                # word longer than line: hard-break
                while len(w) > max_chars:
                    out.append(w[:max_chars])
                    w = w[max_chars:]
                cur = w
        if cur: out.append(cur)
    return out

def draw_message(text, ttl):
    oled_kick()
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    # Top: MSG label + TTL bar + seconds
    oled.text("MSG", 3, 2)
    sec = "{}s".format(int(ttl))
    oled.text(sec, 128 - len(sec) * 8 - 3, 2)
    bar_max = 128 - 32 - 30
    bar_w = int(bar_max * max(0.0, min(1.0, ttl / 60.0)))
    oled.hline(32, 5, bar_w, 1)
    # Body: choose font size based on length
    # Big (2x) font: ~16x16 per char, max ~7 chars/line, max 3 lines (rows 12..60)
    # Small font:    8x8 per char,    max 14 chars/line, max 5 lines
    big_lines = wrap_text(text, 7)
    if len(big_lines) <= 3 and all(len(L) <= 7 for L in big_lines):
        line_h = 18
        total_h = len(big_lines) * line_h
        start_y = 12 + (50 - total_h) // 2
        for i, L in enumerate(big_lines):
            x = (128 - len(L) * 12) // 2
            draw_big(L, x, start_y + i * line_h)
    else:
        lines = wrap_text(text, 14)[:5]
        line_h = 10
        total_h = len(lines) * line_h
        start_y = 12 + (50 - total_h) // 2
        for i, L in enumerate(lines):
            x = (128 - len(L) * 8) // 2
            oled.text(L, x, start_y + i * line_h)
    oled.show()

def draw_big(s, x, y):
    for dy in (0, 1):
        for dx in (0, 1):
            for i, ch in enumerate(s):
                oled.text(ch, x + i * 12 + dx, y + dy)

def draw_inv_pill(x, y, text):
    w = len(text) * 8 + 4
    oled.fill_rect(x, y, w, 9, 1)
    oled.text(text, x + 2, y + 1, 0)
    return w

def seg_bar_brkt(x, y, w, h, pct, seg_w=2, gap=1):
    pct = max(0.0, min(100.0, pct))
    oled.vline(x, y, h, 1)
    oled.vline(x + w - 1, y, h, 1)
    inner_x = x + 2
    inner_w = w - 4
    n_seg = (inner_w + gap) // (seg_w + gap)
    filled = int(round(n_seg * pct / 100))
    for i in range(filled):
        sx = inner_x + i * (seg_w + gap)
        oled.fill_rect(sx, y + 1, seg_w, h - 2, 1)

def short_cat(v, base):
    delta = v - base
    if delta < 0.02:  return "CLN "
    if delta < 0.08:  return "MILD"
    if delta < 0.20:  return "ELEV"
    if delta < 0.40:  return "SMK!"
    return "HVY!"

def draw(v, base, idx, soil_pct, status, blink):
    oled_kick()
    oled.fill(0)
    # === TOP STATUS y=0..8 ===
    draw_inv_pill(0, 0, "AIR")
    # category 4 chars at x=32
    oled.text(short_cat(v, base), 32, 1)
    # blink dot at x=70
    if blink:
        oled.fill_rect(70, 2, 5, 5, 1)
    else:
        oled.rect(70, 2, 5, 5, 1)
    # voltage at x=80
    oled.text("{:.2f}V".format(v), 80, 1)

    # === double-line separator y=9..10 ===
    oled.hline(0, 9, 128, 1)
    oled.hline(0, 10, 128, 1)

    # === AIR bar y=12..19 ===
    oled.text("A", 0, 12)
    seg_bar_brkt(9, 12, 91, 8, idx)
    oled.text("{:>3d}".format(int(idx)), 104, 12)

    # === SOIL bar y=21..28 ===
    oled.text("S", 0, 21)
    seg_bar_brkt(9, 21, 91, 8, soil_pct)
    oled.text("{:>3d}".format(int(soil_pct)), 104, 21)

    # === single separator y=30 ===
    oled.hline(0, 30, 128, 1)

    # === STATUS + SPARKLINE y=32..63 ===
    # tiny status at top of graph area
    oled.text(status, 0, 32)
    gtop, gbot = 41, 63
    gh = gbot - gtop
    oled.hline(0, gtop - 1, 128, 1)
    if history:
        lo = min(history)
        hi = max(history)
        if hi - lo < 0.08:
            hi = lo + 0.08
        step_x = 128 / HIST_LEN
        prev_x = 0
        prev_y = gbot - int((history[0] - lo) / (hi - lo) * gh)
        for j in range(1, len(history)):
            x = int(j * step_x)
            y = gbot - int((history[j] - lo) / (hi - lo) * gh)
            oled.line(prev_x, prev_y, x, y, 1)
            prev_x, prev_y = x, y
        oled.text("{:.2f}".format(hi), 96, gtop)
        oled.text("{:.2f}".format(lo), 96, gbot - 7)
    oled.show()

tick = 0
n_ok = 0
n_err = 0
last_status = "BOOT"
current_msg = None
msg_ttl = 0.0
n_crash = 0
idx_smooth = 0.0
soil_smooth = 0.0
while True:
    wdt.feed()
    t0 = time.ticks_ms()
    try:
        raw, mv, v = read_mq()
        history.append(v)
        if len(history) > HIST_LEN:
            history.pop(0)
        baseline_buf.append(v)
        if len(baseline_buf) > BASELINE_WINDOW:
            baseline_buf.pop(0)
        base = min(baseline_buf)
        idx = max(0.0, min(100.0, (v - base) / max(0.1, MAX_V_AT_3V3 - base) * 100.0))
        soil_raw, soil_pct = read_soil_synth() if USE_SYNTHETIC_SOIL else read_soil()
        idx_smooth += (idx - idx_smooth) * 0.30
        soil_smooth += (soil_pct - soil_smooth) * 0.30

        # Every 10s, force-reinit the OLED (covers the case where the panel
        # silently drops state but I2C ACKs still succeed -- poweron alone
        # won't recover that, only the full init sequence does).
        if tick % 50 == 7:
            oled_reinit()

        if tick % POST_EVERY_N == PLANT_POST_OFFSET and sta.isconnected():
            post_plant(soil_raw, soil_pct)

        if tick % POST_EVERY_N == 0:
            if sta.isconnected():
                try:
                    code = post(raw, mv, idx)
                    if code == 200:
                        n_ok += 1
                        last_status = "#{}".format(n_ok % 1000)
                    else:
                        n_err += 1
                        last_status = "E{}".format(code)
                except Exception as e:
                    n_err += 1
                    last_status = "X" + type(e).__name__[:3]
            else:
                last_status = "NOWIFI"
            print("v={:.3f} mv={:.1f} idx={:.1f} base={:.3f} soil={:.0f}% cat={} {}".format(
                v, mv, idx, base, soil_pct, category(v, base), last_status))

        if tick % MSG_FETCH_EVERY_N == 0 and sta.isconnected():
            new_msg, new_ttl = fetch_msg()
            if new_msg:
                current_msg = new_msg
                msg_ttl = new_ttl
            elif new_msg is None and msg_ttl <= 0:
                current_msg = None

        if current_msg and msg_ttl > 0:
            draw_message(current_msg, msg_ttl)
            msg_ttl -= READ_PERIOD_MS / 1000.0
        else:
            current_msg = None
            draw(v, base, idx_smooth, soil_smooth, last_status, tick % 2 == 0)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        n_crash += 1
        last_status = "C{}".format(n_crash % 1000)
        try:
            sys.print_exception(e)
        except Exception:
            print("loop err:", type(e).__name__, e)
        time.sleep_ms(500)

    tick += 1
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    delay = READ_PERIOD_MS - elapsed
    if delay > 0:
        time.sleep_ms(delay)
