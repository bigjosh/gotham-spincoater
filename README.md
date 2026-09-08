# Gotham spin coater: one-fan bench test

> **Tach problem solved — [The Genius Move: Read the Tach Between the Noise](THE_GENIUS_MOVE.md)**
>
> The builder's PWM-timing insight fixed the reported false-RPM problem on the real fan. Read the story, the implementation, and the six-channel test results.

MicroPython application for Raspberry Pi Pico 2 W on the 52Pi/GeeekPi **Pico Breadboard Kit Plus (EP-0172)**. It sets an ARCTIC P12 Pro's PWM command and displays measured tachometer RPM, pulse frequency, and the highest valid RPM seen since application startup.

## Connect the first fan

Identify the fan connector by its pin numbers and key; wire colors can differ, including all-black cables.

| Fan connector | Connect to |
| --- | --- |
| Pin 1: ground | External 12 V supply negative **and** Pico GND (e.g. physical pin 23) |
| Pin 2: motor power | External regulated **+12 V** supply |
| Pin 3: tachometer | **GP19**, Pico physical pin **25** |
| Pin 4: PWM control | **GP18**, Pico physical pin **24** |

Power the Pico through USB; the fan's motor gets its power from the separate 12 V supply. Do not connect 12 V to a Pico pin or the kit's 3.3/5 V rails. The firmware enables the tach input's internal pull-up to **3.3 V**. An external **4.7 kΩ resistor from GP19 to 3V3(OUT)** is a useful improvement for longer/noisier wiring.

### Current diagnostic: active drive and synchronized tach sampling at 10 kHz

At the user's request, `device/config.py` selects **`PWM_PUSH_PULL = True`, `PWM_HZ = 10000`, and `SYNCHRONOUS_TACH = True`**. GP18 uses hardware PWM to actively drive **0 V and 3.3 V**. The duty is positive: 0% holds LOW, 100% holds HIGH, and intermediate settings have a 100 microsecond period. The screen identifies `SYNC TACH` and `10kHz`; fan #0 is labelled `FAN 00`. The application starts at 0% and uses the same joystick/buttons.

This bench setting is below [ARCTIC's specified 21-28 kHz range](https://support.arctic.de/en/p12-pro). That page does not explicitly guarantee the PWM input's 3.3 V HIGH threshold. On September 8, 2026, the builder confirmed that synchronized sampling completely solved the reported false-RPM problem on the real fan. Maximum-speed characterization remains to be recorded. GP19 remains an input with its internal 3.3 V pull-up.

The previous single-fan 25 kHz open-drain mode is retained: set `PWM_PUSH_PULL = False`, `PWM_HZ = 25000`, and `SYNCHRONOUS_TACH = False` to restore it. In that mode PIO drives LOW or releases GP18, letting the fan supply HIGH; it never drives HIGH. Its direct connection relies on powered RP2350 digital FT input tolerance. ARCTIC specifies up to 5.25 V on its PWM input, and an unpowered Pico does not have the same tolerance. The active-drive experiment is not a general compatibility guarantee for other fans or boards.

### How synchronized tach sampling works

Each hardware PWM wrap sends a delay word through DMA to a PIO state machine. PIO waits until the **midpoint of the longer HIGH or LOW phase**, then reads tach. At 10 kHz the ideal sample point is at least 25 microseconds from either switching edge. DMA and GPIO synchronization add a small hardware latency. The PWM counter continues wrapping at 0% and 100%, so tach sampling continues at those settings too.

PIO counts 100-microsecond samples between successive sampled falling edges and sends **one complete cycle duration** to Python. Python retains the latest eight periods and converts their mean to RPM. It receives about 100 interrupts per second per fan at 3,000 RPM, rather than 10,000 sampling interrupts. Six fans need six PIO state machines and six DMA channels; the sampler program occupies 17 instructions in each used PIO block.

The method rejects disturbances that have settled before the selected sample point. It does not repair voltage levels or reject noise that persists through the midpoint. There is no artificial 4,000-RPM cap. Sampling gives 100-microsecond single-period resolution; the eight-period average spans approximately 80 ms at 3,000 RPM.

Changing duty restarts the affected PWM slice's samplers and clears their averaging windows, avoiding a period spanning a phase change. Consequently very frequent duty changes can keep measurements invalid; continuous measurement during fast closed-loop ramps is future work. Keep `machine.freq()` unchanged while sampling. The current implementation requires 10 kHz hardware PWM and noninverted output.

### Power order

1. Make wiring changes with fan power off.
2. Power the Pico by USB and wait for the stopped test screen.
3. Apply the fan's 12 V supply.
4. When finished, turn **fan power off first**, then unplug the Pico.

For a finished machine, a suitable interface stage or interlocked power arrangement avoids depending on manual power sequencing. Do not transfer this direct connection to an original RP2040 Pico, an ADC-capable GPIO, or a motherboard/hub that pulls the tach signal to a higher voltage.

## Use the screen

- **Joystick left/right:** decrease/increase selected PWM by 1 percentage point; hold to repeat.
- **Joystick up/down:** increase/decrease by 5 points; hold to repeat.
- **BTN1:** start or pause the output at the selected setting.
- **BTN2:** command 0%, disarm output, and reset the selected setting to 0%. Holding it inhibits output.
- The board boots with **0% output**. Adjusting the selection while stopped does not spin the fan.
- `SET PWM` is the selected command; `OUTPUT` shows what is currently applied. This test does not regulate RPM.
- `MEASURED RPM` and `TACH Hz` show live measurements. `--` indicates no valid recent tach signal, not a measurement of zero RPM. A stationary fan and a disconnected tach wire cannot be distinguished from pulse absence alone.
- `PEAK RPM` is the largest valid interval-average measurement since startup. It is a bench observation, not an independently calibrated accuracy claim.

To characterize maximum speed, increase the setting gradually, reach 100%, and let the reading settle for several seconds. The original fan is rated 600–3,000 RPM, but the actual result depends on the fan, supply and mechanical changes. Use current stable RPM as well as the peak; noise or a transient can affect a peak measurement. Keep spinning parts clear and the fan secured.

The tach conversion assumes the standard **two pulses per revolution**: 100 Hz means 3,000 RPM. `PULSES_PER_REV` is configurable. Two complete periods establish a valid reading; the averaging window then grows to eight. Readings become stale after 1.5 seconds without a new period. The old 500-microsecond edge-spacing gate is used only when synchronized tach is disabled.

## Six-fan pin allocation

See [the complete physical header map](PINOUT.md) for all used and free pins, arranged with the Pico's USB socket at the top.

The kit already occupies GP2–17 and GP26/27. GP4 is connected to TFT data-out, and GP10/11 connect to touch reset/interrupt even though those pins are omitted from the manufacturer's short software pin table. Keep those connections reserved.

| Fan | PWM | Tach | Board changes |
| --- | --- | --- | --- |
| 0 | GP18 (pin 24) | GP19 (pin 25) | None; implemented now |
| 1 | GP20 (pin 26) | GP21 (pin 27) | None |
| 2 | GP22 (pin 29) | GP28 (pin 34) | None; tach remains 3.3 V |
| 3 | GP0 (pin 1) | GP1 (pin 2) | None |
| 4 | GP12 (pin 16) | GP16 (pin 21) | Disconnect kit RGB and D1 links first |
| 5 | GP13 (pin 17) | GP17 (pin 22) | Disconnect kit beeper and D2 links first |

This preserves the TFT, capacitive touch, both buttons, and joystick. Reclaiming four auxiliary connections is a future hardware modification, **not required for today's test and not yet performed**. If keeping the RGB/buzzer/indicator LEDs is important, a dedicated fan-controller IC can instead expand the interface. An ordinary low-frequency LED PWM board is not a 25 kHz fan controller.

Only fan #0 is initialized: `ENABLED_CHANNELS = (0,)`. Add connected channels to that tuple when wiring the rig. Enabling #4/#5 also requires `AUX_LINKS_DISCONNECTED = True` after the physical modifications. Synchronous tach uses state machines `(0, 1, 2, 3, 8, 9)`, leaving PIO1 available for wireless. GP12 and GP13 share a PWM timer: one wrap-paced DMA starts a two-transfer chain feeding their respective samplers. Their frequency must match, but duty can differ. Closing one channel preserves its sibling's PWM.

`FanRig` applies the current manual UI command to all enabled fans, shows fan #0 prominently, and adds a compact RPM line for other enabled channels. Individual control is available as `rig.fans[channel].set_duty(percent)`. Automatic ramp/hold and RPM regulation are not implemented yet. The retained open-drain mode is a single-fan fallback. A missing PWM connection can cause a fan to run at full speed; the 0% software command is not a power disconnect.

## Files and board tools

- `device/main.py`: joystick, buttons, startup and application loop.
- `device/ui.py`: TFT layout and changed-field updates.
- `device/display.py`: ST7796S display driver with small drawing buffers.
- `device/open_drain_pwm.py`: low/release PIO PWM.
- `device/fan.py`: fan command and tach measurements.
- `device/sync_program.py`: shared PIO synchronous sampler and cycle counter.
- `device/sync_tach.py`: PWM-wrap DMA pacing, phase selection and resource ownership.
- `device/periods.py`: interrupt-safe rolling mean of complete periods.
- `device/rig.py`: enabled fan construction, shared commands and per-fan readings.
- `device/config.py`: GPIO, display orientation and joystick-direction settings.
- `device/THIRD_PARTY_NOTICES.md`: source attributions and licenses.
- `tools/pico.ps1`: board discovery, deploy, REPL commands, and noninterrupting monitoring.

The helper identifies the project board by USB serial **8792b44d9c11021d** before opening it. **COM4 and COM5 are always excluded**. The last observed project port is COM7, but the helper does not rely on that assignment.

```powershell
.\tools\pico.ps1 -Action Info
.\tools\pico.ps1 -Action Deploy
.\tools\pico.ps1 -Action Launch -Seconds 5
.\tools\pico.ps1 -Action Monitor -Seconds 10
```

`Info`, `Exec`, `Deploy`, and `Launch` interrupt any running application; its cleanup commands the fan off. `Monitor` observes serial telemetry without stopping the application. Deployment backs up overwritten files under `artifacts/`, verifies each upload by SHA-256 readback, and activates staged files after all upload checks pass. The application also runs automatically from `main.py` on boot.

Display controller variants exist across kit lots. If orientation or colors need adjustment, change the display options in `config.py`. The current driver targets ST7796S. Joystick direction signs are also configurable.

Validation details are in `artifacts/validation.md`. `tools/test_sync_board.py` exercises six synchronous samplers on spare GP0/GP1, including an artificial tach signal with switching-edge spikes. It temporarily uses PIO1 SM4 for the signal generator, so run only when wireless is inactive. `tools/selftest.py` is the older GPIO-interrupt test on GP0/GP1 and SM10/11. Do not run either test once those pins are connected to additional fan channels or other hardware. Synthetic signals are not actual fan measurements.

## Sources

- [52Pi kit specification and pinout](https://wiki.52pi.com/index.php?title=EP-0172)
- [Manufacturer's Pico 2 example](https://github.com/geeekpi/pico_breadboard_kit/tree/pico2)
- [Manufacturer's board-link photograph](https://wiki.52pi.com/images/thumb/4/4d/EP-0127-14.jpg/800px-EP-0127-14.jpg)
- [ARCTIC P12 Pro electrical interface](https://support.arctic.de/en/p12-pro)
- [RP2350 datasheet, FT pins and power-dependent limits in §§14.8–14.9](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)
- [Intel QST reference: standard tach pulse count](https://www.intel.com/content/dam/develop/external/us/en/documents/intel-qst-programmers-reference-manual.pdf)
- [Original Intel four-wire fan specification, mirror](https://www.mikrocontroller.net/attachment/625147/intel-4wire-pwm-fans-specs.pdf)
- [MicroPython PIO PWM example](https://github.com/micropython/micropython/blob/v1.29.0/examples/rp2/pio_pwm.py)
