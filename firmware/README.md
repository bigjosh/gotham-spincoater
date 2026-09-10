# Install the controller software

This guide takes a **Raspberry Pi Pico 2 W** from a blank board to the Gotham Spinner dashboard. It works on Windows, macOS, and Linux. For the parts, wiring, and first spin, start with the [main build guide](../README.md).

Keep the fans' **12 V supply off throughout installation**. USB powers the Pico and screen. Close Thonny or any other program using the Pico's serial connection before running these commands.

## 1. Download the project

On the [GitHub repository](https://github.com/bigjosh/gotham-spincoater), choose **Code → Download ZIP**, then extract it. Alternatively:

```sh
git clone https://github.com/bigjosh/gotham-spincoater.git
cd gotham-spincoater
```

Open a terminal in the extracted project folder: the one containing `README.md`, `device`, and `tools`. Run the remaining terminal commands from this folder.

## 2. Install MicroPython on the Pico

Use the **standard ARM build of MicroPython v1.29.0 for Pico 2 W**, the version tested with this project. The Pico, Pico W, Pico 2, and RISC-V images are different downloads.

1. Download [RPI_PICO2_W-20260824-v1.29.0.uf2](https://micropython.org/resources/firmware/RPI_PICO2_W-20260824-v1.29.0.uf2) from the [official Pico 2 W page](https://micropython.org/download/RPI_PICO2_W/). Save the file to your computer.
2. Disconnect the Pico's USB cable. Hold its **BOOTSEL** button, reconnect USB using a data-capable cable, then release BOOTSEL.
3. A removable drive named **RP2350** appears. Copy the downloaded `.uf2` file onto that drive.
4. Wait for the copy to finish. The drive disappears when the Pico reboots into MicroPython. That is expected; subsequent Python files go over USB serial, not onto the bootloader drive.

These are Raspberry Pi's [official UF2 installation steps](https://www.raspberrypi.com/documentation/microcontrollers/micropython.html#drag-and-drop-micropython). Replacing firmware on an already-used board can affect its files; back up anything you need first.

## 3. Set the hardware configuration

Open [device/config.py](../device/config.py) in a text editor. For a first test with fan #0 on an **unmodified kit**, set:

```python
ENABLED_CHANNELS = (0,)
AUX_LINKS_DISCONNECTED = False
```

Keep the comma in `(0,)`. Leave the GPIO assignments and other settings as supplied.

The repository configuration describes the original builder's modified board, so **check these two lines before uploading**. Channels #0–#3 work without the auxiliary modification. Set `AUX_LINKS_DISCONNECTED = True` only after completing and checking the [four auxiliary disconnections](../BOARD_MODIFICATIONS.md) needed for channels #4/#5. You can still start with only #0 selected, then enable other wired fans on the touchscreen.

`ENABLED_CHANNELS` is an initial selection, used when no saved selection exists. On a previously used coater, the saved touchscreen selection in `fans.json` takes precedence; check the tiles before your first START.

## 4. Install the USB upload tool and identify your board

Install [Python 3](https://www.python.org/downloads/) on your computer if needed. The commands below create a local environment and install **mpremote 1.29.0**. No environment activation or system-wide package installation is required.

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install mpremote==1.29.0
.\.venv\Scripts\python.exe -m mpremote connect list
```

**macOS / Linux Terminal:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install mpremote==1.29.0
.venv/bin/python -m mpremote connect list
```

On Linux, install your distribution's Python `venv` package if the first command says it is missing.

The listing shows a port followed by its USB serial number. To identify your Pico, compare the listing with the Pico unplugged and plugged in. Copy its **serial number**, preserving letter case. Listing ports does not open them. Every command below selects this one board explicitly using `id:SERIAL`; do not use an automatic connection when other serial devices are attached. See the [official mpremote connection documentation](https://docs.micropython.org/en/v1.29.0/reference/mpremote.html#commands).

## 5. Upload the application

Replace `YOUR_PICO_USB_SERIAL` below with the serial number you just identified. Paste the block for your operating system into the **same terminal**. It copies all Python modules into the Pico's root directory, copies `main.py` last, then reboots. A copy error stops the remaining steps.

**Windows PowerShell:**

```powershell
$pico = 'id:YOUR_PICO_USB_SERIAL'
& {
    $modules = @(Get-ChildItem -LiteralPath .\device -Filter '*.py' -File |
        Where-Object Name -ne 'main.py' | ForEach-Object FullName)
    .\.venv\Scripts\python.exe -m mpremote connect $pico fs cp @modules :
    if ($LASTEXITCODE -ne 0) { throw 'Module upload failed; do not power the fans.' }
    .\.venv\Scripts\python.exe -m mpremote connect $pico fs cp device/main.py :main.py
    if ($LASTEXITCODE -ne 0) { throw 'main.py upload failed; do not power the fans.' }
    .\.venv\Scripts\python.exe -m mpremote connect $pico reset
}
```

**macOS / Linux Terminal (bash or zsh):**

```sh
pico='id:YOUR_PICO_USB_SERIAL'
modules=()
for file in device/*.py; do
    [ "$file" = 'device/main.py' ] || modules+=("$file")
done
.venv/bin/python -m mpremote connect "$pico" fs cp "${modules[@]}" : &&
.venv/bin/python -m mpremote connect "$pico" fs cp device/main.py :main.py &&
.venv/bin/python -m mpremote connect "$pico" reset
```

The leading `:` in a copy destination means the Pico's filesystem. Only `.py` files are uploaded; the commands preserve saved recipes and fan selections. Copy and reset behavior is documented in the [mpremote reference](https://docs.micropython.org/en/v1.29.0/reference/mpremote.html#commands).

## 6. Check the dashboard and continue to your first spin

After reboot, the screen should show the six fan tiles and an idle controller with zero requested power. No motor starts automatically. The Pico should advertise the open Wi-Fi network **Gotham Spinner**.

Return to the [getting started instructions](../README.md#getting-started) to check the wiring, apply fan power, and run a recipe. After installation, the application starts whenever the Pico is powered; the computer is no longer needed.

If the screen stays blank, inspect the startup output with the appropriate command in the same terminal:

```powershell
# Windows
.\.venv\Scripts\python.exe -m mpremote connect $pico repl
```

```sh
# macOS / Linux
.venv/bin/python -m mpremote connect "$pico" repl
```

`Ctrl-X` exits this monitor. A healthy startup reports `TOUCH_READY` and `GOTHAM_READY`; later status messages show the controller state. If startup messages have already scrolled past, leave fan power off, press `Ctrl-C` to interrupt, then `Ctrl-D` to restart MicroPython and watch the boot. A missing-module error usually means a file was omitted or copied inside a `device` subfolder instead of the root. Repeat the upload block after exiting the monitor. If the port is busy, close other serial monitors. If no port appears, check that BOOTSEL is released and the USB cable carries data.

## Updating an existing coater

Stop the coater and turn off its 12 V fan supply. Download the new project version, retain your hardware choices in `device/config.py`, and repeat the upload and reboot steps. Reinstalling the MicroPython UF2 is only necessary when changing the interpreter version. Saved `recipes.json`, `fans.json`, and their backups stay on the Pico because the upload only copies Python files. Export recipes from the web page before an update if you want an additional backup.

## Maintainer tooling and original installation record

[tools/pico.ps1](../tools/pico.ps1) is the original builder's Windows maintenance helper. It is deliberately locked to that builder's USB serial number and excludes two unrelated local ports. It will not discover or program a new builder's Pico; use the explicit mpremote instructions above.

The original board was flashed on 2026-09-08 with the same Pico 2 W ARM v1.29.0 image. Its downloaded SHA-256 was `c49c7ed93eb7eeaaa78436b17bd4329e5d4ccbaebc1e79503ad75e0a29c4530e`. This records the artifact used; it was not compared with a separately published checksum. The UF2 was obtained over certificate-verified HTTPS and its headers checked. [verification.txt](verification.txt) records the resulting interpreter, board type, and board ID. The cached UF2 is excluded from Git; use the official download above.
