# Board modifications for six fans

An unmodified **52Pi/GeeekPi Pico Breadboard Kit Plus, EP-0172** has enough free pins for **four fans (#0–#3)**. To connect all six, disconnect four kit peripheral branches and use those GPIOs for **fans #4 and #5**. The screen, touch, buttons, and joystick remain connected; the RGB LED, buzzer, and two GPIO indicator LEDs are sacrificed.

You can build and run the first four channels before doing this work. Return to [Getting started](README.md#getting-started) for the complete build, or use the [pin map](PINOUT.md) while tracing the board.

**Before installing the app on an unmodified kit, set `AUX_LINKS_DISCONNECTED = False` in [device/config.py](device/config.py).** The checked-in value is `True` for the completed six-fan rig. Setting a fan to DISABLED on the touchscreen is insufficient: available channels still initialize their GPIOs and tach samplers. Change the flag to `True` only after all four branches below are physically isolated and checked.

This guide gives the GPIO destinations and a tracing procedure. The manufacturer does not publish a verified four-part removal list, and exact resistor references/values have not been recorded for this build. Identify each series connection on your own board before desoldering; do not guess an `Rxx` number from a photograph.

## What to disconnect

Orient the board as in the photograph: Pico socket at the left, its USB end at the top, display at the right. Physical pin numbers refer to the Pico's 40-pin header, not GP numbers; see the [header map](PINOUT.md).

| GPIO to reclaim | Physical pin | Kit connection to isolate | Where to look | New function |
| --- | ---: | --- | --- | --- |
| GP12 | 16 | RGB LED data input | White square RGB LED below the display, immediately right of the joystick; nearby resistor | Fan #4 tach |
| GP13 | 17 | Buzzer driver's GPIO input | Round beeper at lower left; Q1 and nearby resistor bank between it and the joystick | Fan #4 PWM |
| GP16 | 21 | D1 indicator LED branch | Top entry of the small D1–D4 bank between beeper and joystick; marked GP16 | Fan #5 PWM |
| GP17 | 22 | D2 indicator LED branch | Second entry of that bank; marked GP17 | Fan #5 tach |

Assignments are confirmed by the [52Pi pin table](https://wiki.52pi.com/index.php?title=EP-0172) and [manufacturer example README](https://github.com/geeekpi/pico_breadboard_kit#pinout). Locations are visible in the manufacturer's photograph below.

[![52Pi EP-0172 component-location photograph](https://wiki.52pi.com/images/8/89/EP-0127-10.jpg)](https://wiki.52pi.com/index.php?title=File:EP-0127-10.jpg)

*Unmodified manufacturer reference image, hosted by 52Pi; click for its source page. The wiki uses an EP-0127 filename for this image on its EP-0172 product page. Compare the layout and labels with your actual board.*

**Preserve** the entire lower-right **TFT Controller** resistor bank, both buttons, and the joystick connections. Also leave **D3 (3V3) and D4 (5V)** alone: they are supply indicators and do not reclaim GPIOs. GP14/GP15 must remain connected to STOP/START.

The manufacturer describes removing **0Ω resistors** to disconnect connections, but does not identify four dedicated auxiliary 0Ω links in its published instructions. The resistors beside the LEDs and buzzer may instead provide current limiting, series resistance, or transistor bias. **Do not remove a resistor simply because it is nearby, marked zero, or in the same row.** Disabling a peripheral in software does not disconnect its circuit. [Manufacturer's description](https://52pi.com/collections/featured-products/products/raspberry-pi-pico-pico-w-breadboard-kit-with-3-5-inch-touch-screen-display-led-indicator-on-board)

## 1. Prepare the board

You need a multimeter with resistance/continuity ranges, fine probes, magnification, a temperature-controlled soldering iron suitable for small surface-mount parts, flux, fine tweezers, and solder wick. Hot tweezers are useful if available. Save a close-up photograph before starting and keep removed parts separately labeled for restoration.

1. Stop the application, then turn off and disconnect the **fan 12 V supply first**.
2. Disconnect Pico USB, any other kit power supply, and external fan/control wiring. Never measure resistance or desolder on a powered board.
3. If the Pico is socketed, note its orientation and carefully lift it evenly out of both socket rows. This removes the Pico's internal circuitry from the continuity measurements. Do not attempt to pull out a soldered module.
4. Support the kit on a clear work surface. The illustrated peripheral area is on the top side; there is no need to start by removing the TFT. Keep the iron clear of the display, socket plastic, and joystick.

If your board layout differs, use its labels and continuity results to establish the connections. A matching product name alone is insufficient to identify a soldering target.

## 2. Identify each series connection before removing it

The intended change is an open circuit in the **branch from the GPIO to the kit peripheral**, while keeping the Pico socket and breakout header connected:

```text
Pico GPIO socket contact ----+---- kit GPIO breakout ---- fan signal
                            |
                       [series part]   <-- remove this verified connection
                            |
                      kit peripheral
```

Work on one GPIO at a time:

1. Touch the meter probes together and note their resistance. Confirm the GPIO's kit breakout contact has essentially that same resistance to the corresponding Pico socket contact from the table above. Record baseline resistance from the GPIO to ground, each supply rail, and adjacent contacts before changing anything; keep probe polarity consistent for later comparisons.
2. Follow that GPIO's trace into the indicated peripheral area. Find the component that is **in series with this signal branch**. One terminal should connect directly to the GPIO contact; the other should lead to the peripheral input or LED branch. Use resistance readings and visible traces together, rather than relying only on the continuity beep.
3. Confirm both destinations. For the RGB LED, isolate its **data** connection. For the buzzer, isolate the **GP13 input to the driver**, retaining the driver's existing bias network. Establish that the disconnected driver's input will retain a defined **OFF** bias; if that cannot be established, leave this branch unchanged until its circuit is understood. For D1/D2, isolate each LED's own series branch. Removing a shared supply connection or a transistor's bias resistor is not equivalent.
4. Record the component's printed reference and original value, if readable. A verified series resistor can disconnect the branch even if it is not 0Ω. In-circuit resistance can include parallel paths; do not infer an exact replacement value from that reading alone.

**Proceed only when the series path is positively identified.** If the trace disappears into an inaccessible area, the GPIO has additional branches, or the meter results do not establish both ends, resolve that connection from a schematic or further tracing first. Published sources do not currently support an exact “remove Rxx, Ryy…” list for all four signals.

## 3. Remove and inspect

1. Apply a little flux to the identified part's two solder joints.
2. Heat both ends sufficiently to melt the solder. Hot tweezers heat both joints together. With a single iron on a small chip resistor, add a little fresh solder to each end and use a tip that can heat both joints together; lift gently with tweezers only when both ends release freely. If the tool cannot heat both joints, use suitable rework equipment rather than pulling one end against solid solder. Do not pry a cold end or pull against a bonded pad.
3. Let the area cool, then remove excess solder with wick as needed. Leave **two separate, unbridged pads**. Do not fill the gap with solder: that would reconnect the peripheral.
4. Inspect under magnification for a solder bridge, displaced neighboring part, or lifted pad. Label and save the removed component.
5. Perform the following checks before moving to the next GPIO.

| Check, with power and Pico removed | Expected result |
| --- | --- |
| GPIO breakout to its Pico socket contact | Still a direct, near-zero-ohm connection |
| GPIO contact to the GPIO-side pad of the removed part | Still a direct connection |
| GPIO contact to the former peripheral-side pad | No remaining direct connection; normally open for a fully isolated branch |
| Opened pads and neighboring joints under magnification | No solder bridge or displaced part |
| GPIO to adjacent contacts, ground, and supply rails | No newly introduced short; compare with measurements before rework |

Resistance to ground does **not** have to be infinite. LEDs, semiconductor junctions, capacitors, or alternate circuit paths can affect measurements; a beep alone does not establish a short. If a connection remains across the intended break, trace it rather than assuming the modification succeeded. An LED going dark or a buzzer going quiet is not proof of GPIO isolation.

Repeat for GP12, GP13, GP16, and GP17. Photograph the finished work and record the actual references/values for your board revision.

## 4. Reassemble and wire the extra fans

Reinstall a removed Pico in its original orientation with both rows fully aligned. Keep motor power off while wiring. The kit GPIO breakouts remain usable after this method because only their peripheral branches were opened.

| Connection | Fan #4 | Fan #5 |
| --- | --- | --- |
| Fan pin 4, PWM | GP13, physical pin 17 | GP16, physical pin 21 |
| Fan pin 3, tach | GP12, physical pin 16 | GP17, physical pin 22 |
| Fan pin 1, ground | Supply negative and Pico GND | Supply negative and Pico GND |
| Fan pin 2, motor power | External +12 V | External +12 V |

With the Pico USB connector at the top, these pairs place tach above PWM: fan #4 uses the left header and fan #5 the right. See the [complete pinout](PINOUT.md) for all six channels.

Use an individual PWM and tach connection for each fan; do not join tach outputs or use a shared PWM splitter for independently controlled channels. Identify fan connector pins by their key and numbering, since wire colors vary. The [fan wiring and power-order instructions](README.md#3-wire-the-fans) apply to every channel. Each available tach input has an internal 3.3 V pull-up; an optional external 4.7 kΩ pull-up goes to **3V3(OUT)**, never 12 V.

## 5. Enable and check the channels

Keep `AUX_LINKS_DISCONNECTED = False` until the physical work is complete. Once all four branches are isolated and checked, edit [device/config.py](device/config.py):

```python
AUX_LINKS_DISCONNECTED = True
```

Leave the GPIO pairs and state-machine allocation unchanged. Upload the edited configuration and restart the Pico using the [firmware installation instructions](firmware/README.md), with the fan supply still disconnected.

The flag makes fans #4/#5 **available**; their touchscreen ENABLE/DISABLE buttons determine whether they **participate** in a recipe. `ENABLED_CHANNELS` supplies the initial selection on a fresh installation and defaults to #0–#3. Saved touchscreen choices in `fans.json` take precedence on later boots. You do not need to change that setting to enable the new fans by touch.

The flag is your declaration that the four connections have been isolated, **not an electrical test**. An enabled but unwired fan can receive power commands and show a timed RPM warning; missing tach does not disable its PWM output.

1. Confirm the dashboard boots idle and requested power is zero. The new fan tiles should now offer ENABLE rather than LOCKED. Select only the connected fans using their touch buttons; disabled tiles are red. For an incremental test, enable just #4, then repeat with #5. The supply indicators may still light; disconnected RGB/D1/D2 behavior is not a verification test.
2. Secure the fans for an unloaded bench test. Apply external motor power after the idle dashboard appears.
3. Follow [Run your first spin](README.md#5-run-your-first-spin) to check each newly enabled fan's RPM response. Press left START, then press right STOP while the fans run. Every output should command zero; the fans will coast down. Releasing STOP must not restart the run. The tested P12 Pro stops reporting tach at zero PWM, so a displayed zero after STOP does not prove the rotor has stopped.
4. If a fan has missing or implausible tach, stop and remove motor power before checking its individual signal pair and common ground. Do not enable the remaining channels until the connected ones behave correctly.

The repository's synthetic waveform tests are development tools, not installation steps. Some drive GPIOs now used by fan channels; do not run spare-pin tests on the assembled rig. See [Technical reference](TECHNICAL_REFERENCE.md) for validation details.

## Restoring the kit later

1. Stop the run and disconnect motor power. While the kit branches are still isolated, upload `config.py` with `AUX_LINKS_DISCONNECTED = False` and restart the app. This prevents the firmware from initializing fan #4/#5 GPIOs after their kit peripherals are restored.
2. Disconnect USB and all remaining supplies, then remove the fan signal wiring from GP12/GP13/GP16/GP17.
3. Restore the saved components to their original locations and check for shorts before powering up. Use the **original component values**: replace a part with a wire or solder bridge only if it was positively identified as a 0Ω link.
