# Installed MicroPython

- Installed: 2026-09-08
- Board: Raspberry Pi Pico 2 W (RP2350), standard ARM build
- Release: MicroPython v1.29.0, 2026-08-24; latest stable on the official board download page at installation time
- Board download page: https://micropython.org/download/RPI_PICO2_W/
- Download URL: https://micropython.org/resources/firmware/RPI_PICO2_W-20260824-v1.29.0.uf2
- Local cached file (excluded from Git): RPI_PICO2_W-20260824-v1.29.0.uf2; use the download URL above to obtain it
- SHA-256 of downloaded file: c49c7ed93eb7eeaaa78436b17bd4329e5d4ccbaebc1e79503ad75e0a29c4530e

Downloaded directly over certificate-verified HTTPS. Checked UF2 headers and completeness of the RP2350 ARM image before copying to the bootloader volume belonging to board serial 8792B44D9C11021D. The hash records the downloaded artifact; it was not compared against a separately published checksum.

After the board rebooted, Windows assigned COM7. Matched the USB serial and physical USB location before opening only COM7. Queried the running interpreter and confirmed v1.29.0, Raspberry Pi Pico 2 W with RP2350, and board ID 8792b44d9c11021d. See verification.txt for the returned output. Returned to the friendly REPL and closed the serial port.

COM4 and COM5 are unrelated devices and must remain untouched. Re-identify the project board before future access because port and drive assignments can change. No application code was installed.
