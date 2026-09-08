"""Bench-test settings; no fan starts automatically."""
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

# Only channel #0 is constructed by the bench application.
# Channels #4/#5 require disconnecting the kit's RGB, buzzer and D1/D2 links.
# Keep all TFT/touch/joystick/button connections intact.
FUTURE_CHANNELS = ((18, 19), (20, 21), (22, 28), (0, 1), (12, 16), (13, 17))
# One PIO SM and one DMA channel per enabled fan; leave PIO1 for wireless.
TACH_STATE_MACHINES = (0, 1, 2, 3, 8, 9)
ENABLED_CHANNELS = (0,)
# Set True only after physically disconnecting RGB/buzzer/D1/D2 links.
AUX_LINKS_DISCONNECTED = False
