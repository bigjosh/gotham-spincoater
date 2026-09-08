"""Validated six-channel allocation; construct only explicitly enabled fans."""
import config
from fan import Fan


class FanRig:
    def __init__(self):
        ids = tuple(config.ENABLED_CHANNELS)
        if not ids or 0 not in ids or len(ids) != len(set(ids)):
            raise ValueError('Enable unique channels including fan #0 for the TFT')
        if any(type(i) is not int or not 0 <= i < len(config.FUTURE_CHANNELS) for i in ids):
            raise ValueError('Invalid fan channel')
        if any(i >= 4 for i in ids) and not config.AUX_LINKS_DISCONNECTED:
            raise ValueError('Disconnect RGB/buzzer/D1/D2 links before enabling fans #4/#5')
        pins = [pin for i in ids for pin in config.FUTURE_CHANNELS[i]]
        sms = [config.TACH_STATE_MACHINES[i] for i in ids]
        if len(pins) != len(set(pins)) or len(sms) != len(set(sms)):
            raise ValueError('Fan channels must have distinct GPIOs and state machines')
        self.fans = {}
        try:
            for channel in ids:
                pwm, tach = config.FUTURE_CHANNELS[channel]
                self.fans[channel] = Fan(
                    pwm, tach, config.PULSES_PER_REV,
                    push_pull=config.PWM_PUSH_PULL, pwm_hz=config.PWM_HZ,
                    synchronous=config.SYNCHRONOUS_TACH,
                    tach_sm_id=config.TACH_STATE_MACHINES[channel],
                    period_window=config.PERIOD_AVERAGE)
        except BaseException:
            self.close()
            raise

    def set_duty(self, duty):
        # Current manual test applies the same command to all enabled fans.
        # Independent control remains available through fans[channel].set_duty.
        for fan in self.fans.values():
            fan.set_duty(duty)

    def sample(self):
        channels = {i: fan.sample() for i, fan in self.fans.items()}
        primary = dict(channels[0])
        primary['channels'] = channels
        return primary

    def close(self):
        for fan in self.fans.values():
            fan.close()
