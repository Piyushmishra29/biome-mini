from machine import Pin, I2C, ADC
import ssd1306, time

DURATION_S = 60
PERIOD_S = 0.5
N = int(DURATION_S / PERIOD_S)

i2c = I2C(0, sda=Pin(10), scl=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
adc = ADC(Pin(4), atten=ADC.ATTN_11DB)

GRAPH_TOP = 22
GRAPH_BOT = 63
GRAPH_H = GRAPH_BOT - GRAPH_TOP
W = 128

samples = []
v_min = 5.0
v_max = 0.0

def to_y(v, lo, hi):
    if hi <= lo:
        return GRAPH_BOT
    norm = (v - lo) / (hi - lo)
    norm = max(0.0, min(1.0, norm))
    return GRAPH_BOT - int(norm * GRAPH_H)

start = time.ticks_ms()
print("=== MQ-135 SMOKE TEST START ({}s, {}s period) ===".format(DURATION_S, PERIOD_S))

for i in range(N):
    raw = adc.read_u16()
    v = raw * 3.3 / 65535
    samples.append(v)
    v_min = min(v_min, v)
    v_max = max(v_max, v)
    elapsed = time.ticks_diff(time.ticks_ms(), start) / 1000.0
    remaining = max(0, DURATION_S - elapsed)
    print("t={:5.1f}s  raw={:>5}  v={:.3f}  min={:.3f}  max={:.3f}".format(
        elapsed, raw, v, v_min, v_max))

    # Draw
    oled.fill(0)
    oled.text("MQ-135 SMOKE", 0, 0)
    oled.text("t-{:>4.1f}s".format(remaining), 80, 0)
    oled.text("now {:.2f}V".format(v), 0, 10)
    oled.text("pk {:.2f}".format(v_max), 80, 10)
    # Auto-scale Y axis with a small margin
    lo = max(0.0, v_min - 0.05)
    hi = min(3.3, v_max + 0.05)
    if hi - lo < 0.10:
        hi = lo + 0.10
    # Draw graph border
    oled.rect(0, GRAPH_TOP - 1, W, GRAPH_H + 2, 1)
    # Spread samples across the 128 px
    n = len(samples)
    px_per_sample = W / max(N, 1)
    prev_x = 0
    prev_y = to_y(samples[0], lo, hi)
    for j in range(1, n):
        x = int(j * px_per_sample)
        y = to_y(samples[j], lo, hi)
        oled.line(prev_x, prev_y, x, y, 1)
        prev_x, prev_y = x, y
    oled.show()

    # Pace
    target = start + int((i + 1) * PERIOD_S * 1000)
    delay = time.ticks_diff(target, time.ticks_ms())
    if delay > 0:
        time.sleep_ms(delay)

print("=== DONE ===")
print("min={:.3f}V  max={:.3f}V  peak-baseline={:.3f}V".format(
    v_min, v_max, v_max - samples[0]))
oled.text("DONE", 90, 0)
oled.show()
