# Freeing the kit pins for six fans

The unmodified **52Pi/GeeekPi Pico Breadboard Kit Plus, EP-0172** supports fans **#0–#3** with the [current allocation](README.md#six-fan-gpio-allocation). Fans **#4 and #5** need four GPIOs currently connected to kit peripherals. This guide covers identifying and disconnecting those connections, checking the work, and enabling the additional channels.

**Status:** this modification has not yet been performed or electrically verified on the project's board. The GPIO assignments and component locations below come from manufacturer documentation. Exact resistor reference numbers and values for these four connections remain unverified; the procedure deliberately requires tracing them on the actual board before desoldering.

## What to disconnect

Orient the board as in the photograph: Pico socket at the left, its USB end at the top, display at the right. Physical pin numbers refer to the Pico's 40-pin header, not GP numbers; see the [header map](PINOUT.md).

| GPIO to reclaim | Physical pin | Kit connection to isolate | Where to look | New function |
| --- | ---: | --- | --- | --- |
| GP12 | 16 | RGB LED data input | White square RGB LED below the display, immediately right of the joystick; nearby resistor | Fan #4 PWM |
| GP13 | 17 | Buzzer driver's GPIO input | Round beeper at lower left; Q1 and nearby resistor bank between it and the joystick | Fan #5 PWM |
| GP16 | 21 | D1 indicator LED branch | Top entry of the small D1–D4 bank between beeper and joystick; marked GP16 | Fan #4 tach |
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
| Fan pin 4, PWM | GP12, physical pin 16 | GP13, physical pin 17 |
| Fan pin 3, tach | GP16, physical pin 21 | GP17, physical pin 22 |
| Fan pin 1, ground | Supply negative and Pico GND | Supply negative and Pico GND |
| Fan pin 2, motor power | External +12 V | External +12 V |

Use an individual PWM and tach connection for each fan; do not join tach outputs or use a shared PWM splitter for independently controlled channels. Identify fan connector pins by their key and numbering, since wire colors vary. The [fan wiring and power-order instructions](README.md#connect-fan-0) apply to every channel. Each enabled tach input has an internal 3.3 V pull-up; the optional external 4.7 kΩ pull-up goes to **3V3(OUT)**, never 12 V.

## 5. Enable and check the channels

Keep the checked-in configuration unchanged until the physical work is complete. Once all four branches are isolated and all six fans are wired, edit [device/config.py](device/config.py):

```python
AUX_LINKS_DISCONNECTED = True
ENABLED_CHANNELS = (0, 1, 2, 3, 4, 5)
```

For incremental testing, enable only connected fans; for example, `(0, 4)` enables the original fan and the newly wired fan #4. The `AUX_LINKS_DISCONNECTED` flag is a manual declaration that the four connections have been isolated, **not an electrical test**. An enabled but unwired fan can cause the entire recipe to fault on missing tach. Leave `FUTURE_CHANNELS` and the PIO state-machine allocation unchanged.

With motor power still disconnected, power the Pico by USB. From the repository directory, deploy the edited configuration and reboot:

```powershell
.\tools\pico.ps1 -Action Deploy -Only config.py
.\tools\pico.ps1 -Action Reboot
```

After USB reconnects, inspect startup if needed:

```powershell
.\tools\pico.ps1 -Action Monitor -Seconds 10
```

The helper targets this project's identified Pico and excludes COM4/COM5; see [deployment details](README.md#deploy-and-test) before adapting it for another board. These commands are instructions for the completed hardware modification, not actions performed by this documentation update.

1. Confirm the dashboard boots idle, the intended fan tiles are enabled, and requested power is zero. The supply indicators may still light; disconnected RGB/D1/D2 behavior is not a verification test.
2. Secure the fans for an unloaded bench test. Apply external motor power after the idle dashboard appears.
3. Use a short recipe at an achievable RPM to check each newly enabled fan's RPM response. Press left START, then press right STOP while the fans run. Every output should command zero; the fans will coast down. Releasing STOP must not restart the run.
4. If a fan has missing or implausible tach, stop and remove motor power before checking its individual signal pair and common ground. Do not enable the remaining channels until the connected ones behave correctly.

The existing six-channel synthetic test is not a substitute for testing the completed wiring with six physical fans. In particular, do not run the repository's spare-pin waveform tests after wiring additional fans: some drive GP0/GP1/GP20 directly.

## Restoring the kit later

Turn off motor power, disconnect all supplies, and remove the fan signal wiring from the reclaimed GPIOs. Restore the saved components to their original locations and check for shorts before powering up. Use the **original component values**: replace a part with a wire or solder bridge only if it was positively identified as a 0Ω link. Set `AUX_LINKS_DISCONNECTED = False` and remove channels #4/#5 from `ENABLED_CHANNELS` before returning the restored kit to fan-control use.
