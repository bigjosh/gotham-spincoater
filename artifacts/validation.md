# Bench UI validation — 2026-09-08

Board: Raspberry Pi Pico 2 W, MicroPython v1.29.0, USB ID 8792b44d9c11021d, Windows COM7. COM4/COM5 were excluded and never opened.

- All six application Python files uploaded, with SHA-256 readback matching the local files.
- Application initialized ST7796S SPI display, buttons, joystick, GP18 stopped output, GP19 pull-up and tach IRQ without exceptions. A timed three-second run exited cleanly and commanded stopped output.
- Eleven host tach/control tests passed, including exact 3,000 RPM, timing/counter wraps, bounce rejection, startup and timeout.
- PIO backend was assembled with official MicroPython v1.29.0 assembler by the implementation agent and its six instruction words emulated across all 998 intermediate duty settings. Every period was 2,000 clocks; no output-high instructions were used.
- Board synthetic waveform test on GP0 at 50%: median high 19 μs, low 21 μs, total 40 μs; configured/calculated frequency 25,000 Hz. The pulse measurements use the Pico's own clock, not an external calibrated oscilloscope.
- Synthetic 100 Hz PIO signal on GP0 was captured by the real GPIO tach interrupt: 101 accepted pulses, 99.9996 Hz, 2,999.988 RPM. Constant-low and released endpoints, followed by no-pulse timeout, passed.
- Test resources on GP0/GP1 were released, then the normal application was relaunched at 0% output and left running. Startup telemetry reported no tach pulses.

These synthetic tests validate software and the board's internal signal path. Physical TFT readability/orientation and actual fan wiring/RPM require user observation. No actual fan maximum speed has been measured in this session.

## User-requested active-drive / 10 kHz experiment — 2026-09-08

- Configured GP18 for noninverted hardware PWM (`PWM_PUSH_PULL=True`, `PWM_HZ=10000`); GP19 remains tach with `Pin.PULL_UP`. Tach processing is unchanged for comparison.
- All 15 host tests passed, including four new regressions covering push-pull startup/frequency, 0/25/50/100% positive duty and clamps, LOW shutdown/IRQ cleanup, and rejection of nonpositive frequencies. This covers software behavior, not external signal integrity.
- Deployed all six Python files to the identified board on COM7 with SHA-256 readback verification and backups of previous files. COM4/COM5 were excluded.
- `artifacts/verify-push-pull-10khz.py` verified a real `machine.PWM` instance with frequency readback 10000 Hz, duty readback 0, GP18 LOW, GP19 input/pull-up, and GP18 remaining LOW after close. It never requested nonzero duty. No oscilloscope measurement of waveform voltage/timing was made.
- Relaunched the UI with `FAN_UI_READY PWM=GP18 TACH=GP19 MODE=PUSH_PULL HZ=10000 OUTPUT=0%`. Screen labels now identify `PUSH-PULL`, `10kHz`, and fan `00`.
- At startup, telemetry reported approximately 2,745-2,749 RPM despite the 0% command. The current physical PWM-wire connection was not confirmed, so this does not establish that the fan obeys the new drive or stops at 0%. The application was left running for user-controlled testing.
- 10 kHz is below ARCTIC's documented 21-28 kHz range. The false tach problem is not claimed fixed by this deployment.

## PWM-synchronized tach sampling — 2026-09-08

- Installed all ten application Python files with SHA-256 readback verification and backups. Target remained USB serial 8792b44d9c11021d on COM7. COM4/COM5 were excluded throughout.
- 57 host tests passed: 22 fan control/ownership, 10 rolling period measurements, 12 rig configuration/lifecycle, six PIO instruction timing/counting, seven DMA pacing/lifecycle. Host PIO tests interpret the decorated source; real MicroPython assembled and executed it on the board.
- The 17-instruction PIO program samples once per hardware PWM wrap, delayed to the midpoint of the longer phase, then reports exact full falling-to-falling periods in 100-microsecond units. Six readers use SM0/1/2/3/8/9 and six DMA channels. Shared-wrap readers use one PWM DREQ consumer followed by unpaced chained transfers.
- On-board tests caught and fixed MicroPython's lack of array multiplication, and a synthetic generator's out-of-range SET immediate. Confirmed PIO0/2 state machines were all inactive before reclaiming instruction memory left allocated by earlier experiments. The new sampler removes its own program after its last user closes; Launch also releases exact known program objects before unloading modules.
- `tools/test_sync_board.py` drove only spare GP0/GP1. At 25%, 50%, and 75% PWM, six simultaneous samplers each reported exactly 10000 microseconds / 100 Hz / 3000 RPM from a synthetic tach waveform with approximately 3-microsecond glitches at both PWM edges. Each eight-period window filled. Selected ideal sample offsets were 62.5, 25.0, and 37.5 microseconds after wrap. No external oscilloscope timing calibration was performed.
- No-pulse timeout passed after 1.6 seconds. An independent clean 100 Hz generator then verified actual period capture at constant 0% and 100% PWM on all six samplers. Closing the first shared-wrap reader preserved readings on the other five. All test DMA, PIO programs, and GP0/GP1 outputs were released. Full transcript: `sync-board-test.txt`.
- UI launched with `TACH_SYNC=True CHANNELS=(0,) AVERAGE_PERIODS=8`, hardware push-pull 10000 Hz, fan #0 on GP18/GP19, 0% output. Startup reported no tach periods / invalid measurement, not a measured zero shaft speed. Transcript: `sync-ui-launch.txt`.
- Synthetic tests demonstrate capacity and rejection of the injected edge-local disturbances. Actual fan waveform integrity and maximum speed are not established by this test. The manual UI currently resets the affected slice's averaging window when duty changes; frequent closed-loop ramp updates need further work. 10 kHz remains below ARCTIC's specified range.

## Real-fan confirmation — 2026-09-08

After trying the deployed synchronized sampler, the builder reported: **"dude, problem 100% solved!"** This confirms resolution of the reported false-RPM problem on the actual bench fan. No new speed calibration, maximum-RPM value, or six-physical-fan result was supplied. The problem, the builder's timing insight, and the implementation are documented in [The Genius Move](../THE_GENIUS_MOVE.md).
