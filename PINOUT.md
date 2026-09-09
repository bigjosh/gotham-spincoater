# Pico 2 W / EP-0172 header map

View the Pico from above with its USB socket at the top. The middle columns are physical header pin numbers; GP numbers are the names used in MicroPython. This map assumes the kit's original component links remain fitted.

**FREE** means unassigned and not connected to a kit peripheral. **FAN #0** marks the current firmware assignment; it does not confirm external wiring. Kit connections remain occupied even when the application does not use that feature.

| Left header | Pin | Pin | Right header |
| --- | ---: | :--- | --- |
| **GP0 — FREE** | 1 | 40 | VBUS — USB 5 V |
| **GP1 — FREE** | 2 | 39 | VSYS — system power |
| GND | 3 | 38 | GND |
| GP2 — TFT clock | 4 | 37 | 3V3_EN — regulator enable |
| GP3 — TFT data in (MOSI) | 5 | 36 | 3V3(OUT) — 3.3 V supply |
| GP4 — TFT data out (MISO)* | 6 | 35 | ADC_VREF — ADC reference |
| GP5 — TFT chip select | 7 | 34 | **GP28 — FREE**, ADC2 capable |
| GND | 8 | 33 | AGND — analog ground |
| GP6 — TFT data/command | 9 | 32 | GP27 — joystick Y |
| GP7 — TFT reset | 10 | 31 | GP26 — joystick X |
| GP8 — touch I2C data | 11 | 30 | RUN — reset input |
| GP9 — touch I2C clock | 12 | 29 | **GP22 — FREE** |
| GND | 13 | 28 | GND |
| GP10 — touch reset* | 14 | 27 | **GP21 — FREE** |
| GP11 — touch interrupt* | 15 | 26 | **GP20 — FREE** |
| GP12 — RGB LED | 16 | 25 | **GP19 — FAN #0 tach** |
| GP13 — buzzer | 17 | 24 | **GP18 — FAN #0 PWM** |
| GND | 18 | 23 | GND |
| GP14 — BTN2 (stop) | 19 | 22 | GP17 — indicator LED D2 |
| GP15 — BTN1 (START) | 20 | 21 | GP16 — indicator LED D1 |

Header locations follow the [Raspberry Pi Pico 2 W datasheet, figures 2 and 4](https://pip.raspberrypi.com/documents/RP-008304-DS). Peripheral assignments follow the [52Pi EP-0172 documentation](https://wiki.52pi.com/index.php?title=EP-0172). *GP4, GP10 and GP11 are marked on the [manufacturer's board photograph](https://wiki.52pi.com/images/thumb/4/4d/EP-0127-14.jpg/800px-EP-0127-14.jpg), although absent from its short software pin table.

Six GPIOs remain free with fan #0 assigned: **GP0, GP1, GP20, GP21, GP22 and GP28**. Including GP18/19, the unmodified kit leaves eight GPIOs for fans: enough for four independent PWM/tach pairs. The free left-side pair is GP0/GP1.

To reach six independent pairs while keeping the display, touch, buttons and joystick, the existing plan reclaims **GP12, GP13, GP16 and GP17** by disconnecting the kit's RGB LED, buzzer and indicator LED links. This requires physical changes to the kit; disabling them in software does not disconnect them. No such modifications have been made.

See **[Freeing the kit pins for six fans](BOARD_MODIFICATIONS.md)** for component locations, how to identify the correct series connections, removal and continuity checks, and the final wiring/configuration steps. Exact resistor references and values must be confirmed on the actual board.

GP28 supports digital input/output as well as ADC2. In this project's direct fan interface, reserve it for **tach with a 3.3 V pull-up**, not the fan's potentially higher-voltage PWM input. Power, reset and ground pins are not spare GPIOs. GP23–25 and GP29 are not exposed on these headers; they serve the Pico 2 W's internal wireless circuitry.

Current fan allocation was checked against `device/config.py` on 2026-09-08. No firmware or pin assignment was changed to create this map.
