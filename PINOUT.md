# Pico 2 W / EP-0172 header map

View the Pico from above with its USB socket at the top. The middle columns are physical header pin numbers; GP numbers are the names used in MicroPython. This map assumes the kit's original component links remain fitted.

**FAN #n** marks the six-channel firmware allocation; it does not confirm external wiring. **Fans #0–#3 are enabled. Fans #4/#5 are disabled.** A **†** marks a fan signal that requires disconnecting the kit peripheral shown in parentheses before use. Kit connections remain occupied even when the application does not use that feature.

| Left header | Pin | Pin | Right header |
| --- | ---: | :--- | --- |
| **GP0 — FAN #3 PWM** | 1 | 40 | VBUS — USB 5 V |
| **GP1 — FAN #3 tach** | 2 | 39 | VSYS — system power |
| GND | 3 | 38 | GND |
| GP2 — TFT clock | 4 | 37 | 3V3_EN — regulator enable |
| GP3 — TFT data in (MOSI) | 5 | 36 | 3V3(OUT) — 3.3 V supply |
| GP4 — TFT data out (MISO)* | 6 | 35 | ADC_VREF — ADC reference |
| GP5 — TFT chip select | 7 | 34 | **GP28 — FAN #2 tach**, ADC2 capable |
| GND | 8 | 33 | AGND — analog ground |
| GP6 — TFT data/command | 9 | 32 | GP27 — joystick Y |
| GP7 — TFT reset | 10 | 31 | GP26 — joystick X |
| GP8 — touch I2C data | 11 | 30 | RUN — reset input |
| GP9 — touch I2C clock | 12 | 29 | **GP22 — FAN #2 PWM** |
| GND | 13 | 28 | GND |
| GP10 — touch reset* | 14 | 27 | **GP21 — FAN #1 tach** |
| GP11 — touch interrupt* | 15 | 26 | **GP20 — FAN #1 PWM** |
| **GP12 — FAN #4 PWM†** (RGB LED) | 16 | 25 | **GP19 — FAN #0 tach** |
| **GP13 — FAN #5 PWM†** (buzzer) | 17 | 24 | **GP18 — FAN #0 PWM** |
| GND | 18 | 23 | GND |
| GP14 — BTN2 (stop) | 19 | 22 | **GP17 — FAN #5 tach†** (LED D2) |
| GP15 — BTN1 (START) | 20 | 21 | **GP16 — FAN #4 tach†** (LED D1) |

Header locations follow the [Raspberry Pi Pico 2 W datasheet, figures 2 and 4](https://pip.raspberrypi.com/documents/RP-008304-DS). Peripheral assignments follow the [52Pi EP-0172 documentation](https://wiki.52pi.com/index.php?title=EP-0172). *GP4, GP10 and GP11 are marked on the [manufacturer's board photograph](https://wiki.52pi.com/images/thumb/4/4d/EP-0127-14.jpg/800px-EP-0127-14.jpg), although absent from its short software pin table.

## All six fan pairs

| Fan | PWM GPIO (physical pin) | Tach GPIO (physical pin) | Current status |
| --- | --- | --- | --- |
| **#0** | GP18 (24) | GP19 (25) | Enabled |
| **#1** | GP20 (26) | GP21 (27) | Enabled |
| **#2** | GP22 (29) | GP28 (34) | Enabled; tach pull-up to 3.3 V only |
| **#3** | GP0 (1) | GP1 (2) | Enabled |
| **#4** | GP12 (16) | GP16 (21) | Disabled; isolate RGB data and D1 branches first |
| **#5** | GP13 (17) | GP17 (22) | Disabled; isolate buzzer-driver input and D2 branches first |

The eight GPIOs without kit peripheral connections are **GP0, GP1, GP18, GP19, GP20, GP21, GP22 and GP28**. They are now assigned to four enabled PWM/tach pairs. There is no fifth free pair while keeping all original kit connections. The left-side pair, GP0/GP1, serves fan #3.

To reach six independent pairs while keeping the display, touch, buttons and joystick, the existing plan reclaims **GP12, GP13, GP16 and GP17** by disconnecting the kit's RGB LED, buzzer and indicator LED links. This requires physical changes to the kit; disabling them in software does not disconnect them. No such modifications have been made.

See **[Freeing the kit pins for six fans](BOARD_MODIFICATIONS.md)** for component locations, how to identify the correct series connections, removal and continuity checks, and the final wiring/configuration steps. Exact resistor references and values must be confirmed on the actual board.

GP28 supports digital input/output as well as ADC2. In this project's direct fan interface, reserve it for **tach with a 3.3 V pull-up**, not the fan's potentially higher-voltage PWM input. Power, reset and ground pins are not spare GPIOs. GP23–25 and GP29 are not exposed on these headers; they serve the Pico 2 W's internal wireless circuitry.

Current fan allocation was checked against `device/config.py` on 2026-09-08. Enabling fans #0–#3 keeps the original six-channel pin allocation unchanged.
