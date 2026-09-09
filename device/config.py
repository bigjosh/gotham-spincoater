"""Gotham Spinner hardware settings; no fan starts automatically."""
PWM_PIN = 18
TACH_PIN = 19
PWM_PUSH_PULL = True  # Actively drive 0 V and 3.3 V.
PWM_HZ = 10000        # User-requested synchronous sampling test.
SYNCHRONOUS_TACH = True
PERIOD_AVERAGE = 8
PULSES_PER_REV = 2  # Standard four-wire PC fan; verify if using another motor.
DISPLAY_ROTATION = 1
DISPLAY_INVERSION = True
DISPLAY_BGR = True
BUTTON_START = 15  # Kit BTN1, active low.
BUTTON_STOP = 14   # Kit BTN2, active low.
JOYSTICK_X = 26
JOYSTICK_Y = 27
JOYSTICK_X_SIGN = 1
JOYSTICK_Y_SIGN = -1

# The first four fan pairs are available without modifying the kit.
# The builder confirmed RGB/buzzer/D1/D2 isolation for channels #4/#5.
# Keep all TFT/touch/joystick/button connections intact.
# Each tuple is (PWM, tach). With USB at the top, PWM is immediately below
# tach on the same header, except fan #2's unchanged GP22/GP28 pair.
FUTURE_CHANNELS = ((18, 19), (20, 21), (22, 28), (1, 0), (13, 12), (16, 17))
# One PIO SM and one DMA channel per enabled fan; leave PIO1 for wireless.
TACH_STATE_MACHINES = (0, 1, 2, 3, 8, 9)
# Initial selection only; touchscreen choices persist separately in fans.json.
ENABLED_CHANNELS = (0, 1, 2, 3)
# Hardware isolation confirmed by the builder on 2026-09-08.
# Use False on an unmodified kit; True unlocks touch selection for #4/#5.
AUX_LINKS_DISCONNECTED = True
WIFI_SSID = 'Gotham Spinner'
WIFI_IP = '192.168.4.1'
