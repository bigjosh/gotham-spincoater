# Gotham Spinner

**Six samples at once. A parts budget under US$100. About 1–2 hours to build.**

Gotham Spinner is a DIY spin coater built from six PC fans, a Raspberry Pi Pico 2 W, and a touchscreen breadboard kit. It spins up to six samples through the same programmable speed profile, while measuring and adjusting each fan independently.

The advantage over a single-position spin coater is throughput: prepare a batch of six samples in one run instead of repeating the same recipe six times. Everyday parts keep the build inexpensive, and a phone or laptop is all you need to edit recipes—no app, account, or Internet connection required. Choose anywhere from one to six fans, set your ramp and hold times, then start at the bench.

The budget and build time are estimates for a basic assembly with parts and tools on hand and some soldering experience. Sourcing, shipping, tax, custom sample holders, and enclosure fabrication can change the total. This project provides the controller, wiring, and software; the sample mount and splash containment must suit your samples and coating material.

## Getting started

The steps below take you from a fresh kit to a first dry spin, then a six-position run. You can start with one fan and add the others later. No firmware development is needed.

### 1. Gather the parts and tools

| Quantity | Part | What to choose |
| ---: | --- | --- |
| 1 | [Raspberry Pi Pico 2 W with headers](https://www.adafruit.com/product/6315) | The **Pico 2 W**, with both 20-pin headers fitted. The original Pico/Pico W uses a different chip. |
| 1 | [52Pi/GeeekPi Pico Breadboard Kit Plus, EP-0172](https://52pi.com/collections/rpi-pico/products/raspberry-pi-pico-pico-w-breadboard-kit-with-3-5-inch-touch-screen-display-led-indicator-on-board) | The version with the 3.5-inch touch TFT, buttons, and joystick. Choose the kit without an older Pico bundled in. |
| 6 | [ARCTIC P12 Pro fans](https://www.arctic.de/en/P12-Pro/ACFAN00305A) | 12 V, four-pin PWM, rated up to 3,000 RPM. Start with one if you prefer. A [PST five-pack](https://www.arctic.de/en/P12-Pro-PST-5-Pack/ACFAN00307A) plus one fan is another sourcing option; leave the PST splitter sockets unused. |
| 1 | Regulated 12 V DC power supply | At least **3 A** for six fans. Their [rated currents](https://www.arctic.de/media/f4/9f/31/1750412251/Spec_Sheet_P12_Pro_EN.pdf) total 1.98 A; the extra capacity provides headroom. |
| 1 | Micro-USB **data** cable | Connects the Pico to your computer for installation and supplies its USB power. |
| 1, optional | 5 V USB power adapter | Powers the Pico and screen without a computer after installation; an existing suitable USB adapter is sufficient. |
| 6 sets | Four-pin fan connectors/breakout leads, hookup wire, and power terminals | Each fan needs its own PWM and tach connections. Only the 12 V and ground distribution are shared. |
| As needed | Rigid base, fasteners, sample holders, and splash containment | Keep each sample centered over its fan hub and contain thrown liquid. Plan these around your sample size and material. |

For budgeting, the kit and a Pico with headers were listed at approximately **$28 and $8** on September 10, 2026. Fan value packs and simple fixtures help keep the complete build below $100; check your full parts basket before ordering. Tools are not included in that estimate.

You will also need a computer, a small screwdriver, wire cutters/strippers, and a multimeter. The six-fan modification needs a soldering iron suitable for small surface-mount parts, flux, tweezers, and magnification. Using a Pico with pre-soldered headers saves an assembly step.

### 2. Assemble the base and prepare the kit

1. With all power disconnected, socket the Pico 2 W into the kit in the orientation shown by its markings. Check that both header rows are fully seated and no pins are offset.
2. Secure the fan frames to a rigid base with their hub/sample surfaces facing upward. A 3 × 2 arrangement matches the dashboard. Keep moving parts clear of the base, wiring, and neighboring stations.
3. Fit a centered, balanced sample holder and splash containment for each station. Check that the holder and sample clear the fan frame through a full turn. Do the first test **dry, with no sample or liquid**; validate your sample attachment separately before coating. There is no universal chuck drawing or blade-removal procedure in this repository.
4. Decide whether to start with the stock kit or build all six channels now. One to four fans need no kit modification; use `AUX_LINKS_DISCONNECTED = False` when configuring the app in step 4. For all six, follow [the board modification guide](BOARD_MODIFICATIONS.md) to isolate the RGB, buzzer, D1, and D2 GPIO branches. Use `True` only after its continuity checks pass.

**The checked-in configuration is for an already-modified six-fan kit. Change that flag before uploading to an unmodified board.** Disabling a fan on the touchscreen does not electrically disconnect the kit's peripherals.

### 3. Wire the fans

Keep USB and fan power disconnected while wiring. Identify fan connector pins from the connector key and [manufacturer's pin diagram](https://support.arctic.de/p12-pro), not cable color—some cables are all black.

| Fan connector pin | Connect to |
| --- | --- |
| **1 — Ground** | 12 V supply negative **and a Pico GND**, such as physical pin 23 |
| **2 — +12 V** | External 12 V supply positive |
| **3 — Tach** | That fan's tach GPIO from the table below |
| **4 — PWM** | That fan's PWM GPIO from the table below |

For your first fan, connect **PWM to GP18** and **tach to GP19**. GP numbers identify GPIOs; the numbers in parentheses below are physical positions on the Pico's 40-pin header.

| Fan tile | PWM GPIO (physical pin) | Tach GPIO (physical pin) | Kit modification |
| --- | --- | --- | --- |
| **#0** | GP18 (24) | GP19 (25) | None |
| **#1** | GP20 (26) | GP21 (27) | None |
| **#2** | GP22 (29) | GP28 (34) | None |
| **#3** | GP1 (2) | GP0 (1) | None |
| **#4** | GP13 (17) | GP12 (16) | Isolate RGB and buzzer branches |
| **#5** | GP16 (21) | GP17 (22) | Isolate D1 and D2 branches |

With the Pico's USB connector at the top, each adjacent pair has **tach above PWM**. Fan #2 is the exception. The [complete header map](PINOUT.md) also shows the display, touch, buttons, joystick, and power pins.

The Pico gets power from USB; the fans get power from the external 12 V supply. **Never put 12 V on a Pico GPIO or the kit's 3.3/5 V rails.** Join the grounds. Each tach input uses the Pico's internal 3.3 V pull-up. Do not combine tach wires or daisy-chain PWM through a fan splitter: each fan needs independent control and measurement.

This is the tested direct **3.3 V, push-pull, 10 kHz** interface for this Pico 2 W/P12 Pro build. It operates below ARCTIC's specified PWM frequency range; substitution of a different Pico or fan requires checking its interface. See [electrical details](TECHNICAL_REFERENCE.md#electrical-interface).

### 4. Install MicroPython and the app

Leave the fan's 12 V supply off throughout installation.

1. [Download the project](https://github.com/bigjosh/gotham-spincoater/archive/refs/heads/main.zip) and extract it, or clone this repository.
2. Open [device/config.py](device/config.py) in a text editor. Set `AUX_LINKS_DISCONNECTED` for your actual hardware as described above. For a first test with only fan #0 selected, set `ENABLED_CHANNELS = (0,)`.
3. Hold the Pico's **BOOTSEL** button while connecting USB. Install the tested **MicroPython v1.29.0 ARM build for Pico 2 W** using the [firmware installation guide](firmware/README.md).
4. Follow that guide to identify **your** Pico's serial connection and copy the `.py` files inside `device/` to the Pico's top-level filesystem, with `main.py` last. This is a one-time software installation; it does not require editing the control code.
5. Reset the Pico. The six-tile dashboard should appear in **IDLE**, with every requested power at **0%**. `main.py` starts automatically on future boots.

The installation guide has Windows, macOS, and Linux commands. The repository's `tools/pico.ps1` helper is specific to the original builder's board; new builders should use the portable instructions instead.

### 5. Run your first spin

1. Keep the fan hubs empty and dry. Power the Pico by USB first and wait for the idle dashboard, then apply the fan's **12 V** supply.
2. On the touchscreen, enable **only fan #0** for the first test. A tile showing **DISABLE** is already enabled; a tile showing **ENABLE** is disabled. Tap the button in its upper-right corner to change it. These choices save automatically.
3. On your phone or laptop, join **Gotham Spinner**, with **no password**. Stay connected when your device reports that the network has no Internet.
4. Open the captive-portal prompt, or type **[http://192.168.4.1/](http://192.168.4.1/)** into a browser. Use `http`, not `https`.
5. Click **New**, name the recipe **First spin**, and enter the three steps below. **Slew** is the time for the target to ramp from the previous speed; **dwell** is the time to hold the new target. Click **Save to coater**.

| Step | Target RPM | Slew (s) | Dwell (s) |
| --- | ---: | ---: | ---: |
| 1 | 0 | 0 | 0 |
| 2 | 1,000 | 10 | 5 |
| 3 | 0 | 5 | 0 |

6. Keep clear of rotating parts and press the physical **left START** button. Fan #0 should begin turning; its RPM number and power bar should respond. The other five outputs should stay at zero.
7. Press the physical **right STOP** button while it runs. Every PWM output is forced LOW. Wait until the fan visibly stops; releasing STOP must not restart it. Press START again to let the short recipe finish by itself.
8. Check each additional fan in the same way, one tile at a time. Once all connected stations behave correctly, enable the set you want to run together. If #4/#5 say **LOCKED**, complete the kit modification and upload the corresponding configuration first.
9. After the dry checks, fit your secured samples and suitable splash containment. Use a recipe and dispensing method appropriate to your coating material. Keep liquid away from the motor and electronics and use the ventilation required by that material.

For the original 3,000 RPM / 30-second hold, select **Default** and save it, or import [examples/recipes.json](examples/recipes.json). That recipe ramps to 3,000 RPM over 15 seconds, requests a 30-second hold, then ramps to zero over 15 seconds. Each fan adjusts independently toward the shared target; a hold starts on schedule even if a fan is still catching up.

At shutdown, turn the **fan 12 V supply off first**, then disconnect Pico USB. A disconnected PWM wire can make a powered fan run at full speed, so change wiring only with power removed.

## Using the dashboard and recipes

![Gotham Spinner dashboard preview with six fan tiles and physical START/STOP labels](artifacts/dashboard-preview.png)

*Rendered preview of the actual dashboard code. The bottom START/STOP labels identify the physical buttons; they are not touchscreen controls.*

- **Six RPM readings and power bars:** each enabled fan follows the same target with its own speed correction. Selection changes are allowed only while stopped.
- **Red enabled tile:** RPM has stayed outside the allowed band. Defaults are **5% for 2 seconds**. The fan keeps trying to reach the target, and red clears when its RPM returns within bounds. Disabled tiles are also red and show **ENABLE**.
- **Recipes:** 2–9 steps, beginning and ending at 0 RPM. Create, duplicate, edit, and import/export JSON in the browser. Changes take effect after **Save to coater**, while idle. Recipe settings and fan selections survive power cycles.
- **Physical START/STOP:** starts are local to the bench. STOP overrides every motor output independently of the Python UI. An interrupted recipe never resumes automatically. The joystick is unused by this application.

**A displayed zero does not prove the rotor is stationary.** Values below the configurable **Show zero below** threshold (60 RPM by default) display as zero, as do missing/stale measurements. On the tested P12 Pro, the tach signal goes quiet at 0% PWM even though capture remains active; the last reading expires after 1.5 seconds. Expect this during STOP and on disabled fans. Wait for visible standstill before handling a sample. [Measured behavior and explanation](artifacts/validation.md#physical-tach-at-zero-pwm--2026-09-09).

## If your first spin does not work

| Symptom | Check |
| --- | --- |
| No display after installing | Confirm Pico **2 W** firmware, a USB data cable, and app files at the Pico's filesystem root. Recheck Pico orientation and kit connections. The [install guide](firmware/README.md) shows how to inspect startup errors. |
| Wi-Fi connects but no page opens | Stay on **Gotham Spinner** despite the no-Internet warning and enter **http://192.168.4.1/** directly. Disable automatic switching to another network for this connection. |
| START does nothing | Enable at least one wired fan, release STOP, and press START again. Motor start is not available from the browser. |
| Fan runs but has no/implausible RPM | Stop and remove motor power. Check that fan pins 3/4 go to that fan's tach/PWM GPIOs and that supply and Pico grounds are joined. Check the [header map](PINOUT.md), not wire colors. |
| Fan stays red | Confirm a realistic target for its load and allow time to accelerate. Red is an RPM warning, not an automatic shutdown. The UI accepts up to 4,000 RPM, but the P12 Pro is rated to 3,000 RPM. |
| Fans #4/#5 are LOCKED | Their GPIOs still belong to kit peripherals in the stock configuration. Follow [the modification guide](BOARD_MODIFICATIONS.md); changing the flag alone is not the hardware modification. |

## How it works, and where to go next

> **[The Genius Move: Read the Tach Between the Noise](THE_GENIUS_MOVE.md)** tells the story of the timing insight that made fan-speed measurement reliable: sample tach halfway through the longer PWM phase, away from switching disturbances.

The Pico's programmable I/O hardware handles PWM and tach timing. One CPU core adjusts the six fan speeds; the other handles the touchscreen and Wi-Fi editor. The [technical reference](TECHNICAL_REFERENCE.md) explains the implementation, settings, APIs, and development tests.

| Document | Use it for |
| --- | --- |
| [PINOUT.md](PINOUT.md) | Every header pin and all six fan pairs |
| [BOARD_MODIFICATIONS.md](BOARD_MODIFICATIONS.md) | Freeing the four kit GPIOs needed for fans #4/#5 |
| [Firmware and app installation](firmware/README.md) | A fresh Pico 2 W, portable upload commands, and updates |
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) | Recipe details, electrical interface, PIO/control architecture, and APIs |
| [Validation record](artifacts/validation.md) | Test results, measured behavior, and development history |
| [Third-party notices](device/THIRD_PARTY_NOTICES.md) | Credits and licenses for adapted driver code |
