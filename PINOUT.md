# Pico 2 W / EP-0172 header map

Use this map to wire a **Raspberry Pi Pico 2 W** on the **52Pi/GeeekPi Pico Breadboard Kit Plus (EP-0172)**. For the whole build, start with [Getting started](README.md#getting-started).

**Fans #0–#3 use pins that are free on an unmodified kit. Fans #4/#5 require the [board modifications](BOARD_MODIFICATIONS.md).** Before installing the app on an unmodified kit, set `AUX_LINKS_DISCONNECTED = False` in [device/config.py](device/config.py). The repository ships with `True` for the completed rig; set it to `True` on your build only after isolating and checking all four peripheral branches. Disabling a fan on the touchscreen does not release its GPIOs.

## All six fan pairs

| Fan | PWM GPIO (physical pin) | Tach GPIO (physical pin) | Kit modification required |
| --- | --- | --- | --- |
| **#0** | GP18 (24) | GP19 (25) | None |
| **#1** | GP20 (26) | GP21 (27) | None |
| **#2** | GP22 (29) | GP28 (34) | None; tach pull-up must be 3.3 V |
| **#3** | GP1 (2) | GP0 (1) | None |
| **#4** | GP13 (17) | GP12 (16) | Isolate buzzer and RGB LED GPIO branches |
| **#5** | GP16 (21) | GP17 (22) | Isolate D1 and D2 indicator LED branches |

Each fan needs its **own PWM and tach connections**. Fan connector pin 4 goes to PWM, pin 3 to tach, pin 2 to external +12 V, and pin 1 to supply negative and Pico GND. Identify the keyed connector's pin numbering rather than relying on wire colors. See [fan wiring](README.md#3-wire-the-fans) for the connection diagram and power order.

The checked-in initial selection is #0–#3; the first-spin guide changes it to just #0. Touch each available tile's ENABLE/DISABLE button to choose the fans that participate in a recipe; selection is saved across reboots. With `AUX_LINKS_DISCONNECTED = False`, #4/#5 remain LOCKED and their GPIOs are not initialized as fan channels.

## Full header map

View the Pico **from above, with its USB socket at the top**. The middle columns are physical header pin numbers; `GP` numbers are GPIO names, as used in MicroPython and the kit's breakout labels. A **†** marks a GPIO that must be reclaimed from its listed kit connection.

Each fan has tach above PWM on the same header, with **PWM closer to the bottom**. Fan #2 is the exception: GP22 and GP28 are not adjacent.

| Left header | Pin | Pin | Right header |
| --- | ---: | :--- | --- |
| **GP0 — FAN #3 tach** | 1 | 40 | VBUS — USB 5 V |
| **GP1 — FAN #3 PWM** | 2 | 39 | VSYS — system power |
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
| **GP12 — FAN #4 tach†** (RGB data) | 16 | 25 | **GP19 — FAN #0 tach** |
| **GP13 — FAN #4 PWM†** (buzzer) | 17 | 24 | **GP18 — FAN #0 PWM** |
| GND | 18 | 23 | GND |
| GP14 — BTN2 (STOP) | 19 | 22 | **GP17 — FAN #5 tach†** (D2 LED) |
| GP15 — BTN1 (START) | 20 | 21 | **GP16 — FAN #5 PWM†** (D1 LED) |

Header locations follow the [Raspberry Pi Pico 2 W datasheet, figures 2 and 4](https://pip.raspberrypi.com/documents/RP-008304-DS). Peripheral assignments follow the [52Pi EP-0172 documentation](https://wiki.52pi.com/index.php?title=EP-0172). *GP4, GP10 and GP11 are marked on the [manufacturer's board photograph](https://wiki.52pi.com/images/thumb/4/4d/EP-0127-14.jpg/800px-EP-0127-14.jpg), although absent from its short software pin table.

## Used and free pins

The stock kit's free GPIOs are **GP0, GP1, GP18, GP19, GP20, GP21, GP22, and GP28**. Fans #0–#3 use all eight. The six-fan build uses four additional GPIOs reclaimed from the kit; **no header GPIO remains unassigned**. The display, touch, buttons, and joystick keep their original connections.

GP4, GP10, and GP11 remain reserved for the screen/touch assembly even if a particular driver does not use them. Power, reset, and ground pins are not spare GPIOs. GP23–25 and GP29 are not exposed on these headers; they serve the Pico 2 W's internal wireless circuitry.

GP28 supports digital input as well as ADC2. In this direct fan interface, use it for **tach with a 3.3 V pull-up**, not the fan's potentially higher-voltage PWM input. All tach inputs use an internal 3.3 V pull-up; if adding an external pull-up, connect it to **3V3(OUT), physical pin 36**, never 12 V or 5 V.

STOP and DISABLE actively hold the fan's PWM LOW while tach sampling continues. They do not make its assigned pins available to other devices. GPIO allocation is defined in [device/config.py](device/config.py); changing it also requires checking the kit connections and PIO assumptions described in [Technical reference](TECHNICAL_REFERENCE.md).
