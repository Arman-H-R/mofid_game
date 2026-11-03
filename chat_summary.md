# Lidar ball-throw project: chat summary (2026-11-03)

## Setup
- Sensor: LDROBOT STL27L (code calls it "D800"), USB-serial adapter on **COM14**, 921600 baud, ~10 Hz, ~2178 points per turn.
- Use: balls (10–15 cm) thrown at a wall. The lidar plane is parallel to the wall, lidar near the floor (y = up, floor line at y ≈ -50 mm).
- Output: OSC `/touch [x, y, id, state]` to TouchDesigner at 127.0.0.1:7000 (state 1 = hit/down, 0 = move, -1 = gone/up).
- Only ONE lidar. Also have an ESP-12F (ESP8266) and Arduino IDE with esp8266 core 3.1.2.

## Files (C:\Users\Arman\Desktop\P.project\Wenods\mofid_games\market_target\LIdar)
- `lib.py`: serial reader, builds full scans (`get_scans()` returns all waiting scans).
- `use_lib.py`: detection + window. Polar background, blobs, tracker, ball mode, OSC. Flags: `--headless`, `--port`, `--time`, `--no-osc`, `--settings`.
- `settings.py` / `settings_ui.py`: all tunable values + the graphic side panel (tabs Area / Angle / Detect / Speed / Ball / Output). Saved to `settings.json`.
- `lidar_speed/lidar_speed.ino`: ESP-12F sketch that holds the lidar at 13 Hz (new, not yet tried on hardware).
- Originals (untouched): `mellat\D800 arman`.

## Ball mode (how it works)
- Hit = first scan that sees the ball; x, y frozen, no MOVE events.
- "Delay after hit" slider (hit_delay), optional Delay radius, Ball size offset, solid hit marker shown for show_hit seconds (no fade).
- A ball still being tracked (falling/rolling) cannot make a new hit near it.
- Every scan is processed (no skipped scans; last test: dropped 0).

## Current settings.json (set by the user)
mode ball, hit_delay 0.1, hit_radius 0, ball_size 20, show_hit 0.2, min_width 10, confirm_scans 1, circle area r = 3000 at (0, 0), rotation -2.5, min_distance 10, calibration_time 15.

## Findings
- **Fast throws missed = sampling limit, not software.** The lidar looks at each spot once per 100 ms; a fast ball is in the thin laser plane for less than that. A replay showed no ball dropped by filters or delay.
- Moved the lidar plane ~12–15 cm off the wall: **8 of 10 fast throws caught**, each seen in exactly one scan.
- **2 false hits** in the empty area: single noise points at the 3 m edge of the area. Fix: Detect → Smallest object 30 mm (balls give 18–42 points) or a smaller / rectangle area.

## Next step in progress: spin the lidar at 13 Hz (ESP-12F)
STL27L manual facts: pins 1 Tx, 2 PWM, 3 GND, 4 P5V. PWM input 0–3.3 V, 20–50 kHz (30 kHz recommended). External control starts when duty is 45–55 % for ≥ 100 ms, then stays until power-off. Speed must be closed-loop on the speed field in the data packets. PWM grounded = internal 10 Hz.

Sketch: PWM on GPIO4 (D2) at 30 kHz, reads lidar Tx on GPIO13 (D7, `Serial.swap()`), PI loop KP 20 / KI 50 to TARGET_HZ 13, duty 20–100 %, LED (GPIO2) on when locked, Wi-Fi off. Compiles for Generic ESP8266 and NodeMCU 1.0.

Wiring (lidar off): Tx → adapter RX **and** ESP GPIO13; PWM → ESP GPIO4 (off GND); GND → adapter GND **and** ESP GND; 5V as now. Bare ESP-12F needs its own 3.3 V ≥ 300 mA supply, EN/RST 10 kΩ to 3.3 V, GPIO15 10 kΩ to GND, GPIO0 to GND only while flashing.

To do:
1. Flash + wire the ESP.
2. Run `use_lib.py`: top line should show `Lidar: ~13.0Hz`, `Points: ~1660`. Press **B** to rescan the background.
3. 10 fast throws, compare with 8 / 10 at 10 Hz.
4. Back to 10 Hz: PWM wire to GND again and power-cycle the lidar.

## Working rules
- Ask before any physical test (touch / throw) and wait for the user to say "Done".
- Only one program can open COM14; close the window/other scripts first. Never kill the user's processes (e.g. `live_trade.py` is unrelated).
- Test helpers from this chat (ball_session.py recorder, ball_replay.py, miss_check.py) are in the old chat's temp scratchpad folder and may not be available in a new chat.
