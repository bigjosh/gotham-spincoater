"""On-board configuration check; never commands a nonzero fan duty."""
import sys
for module in ('main', 'ui', 'fan', 'open_drain_pwm', 'display', 'config'):
    sys.modules.pop(module, None)
import config
from machine import PWM
from fan import Fan

assert config.PWM_PIN == 18 and config.TACH_PIN == 19
assert config.PWM_PUSH_PULL is True and config.PWM_HZ == 10000
fan = Fan(config.PWM_PIN, config.TACH_PIN, config.PULSES_PER_REV,
          push_pull=config.PWM_PUSH_PULL, pwm_hz=config.PWM_HZ)
try:
    assert isinstance(fan._pwm, PWM)
    assert fan._pwm.freq() == 10000
    assert fan._pwm.duty_u16() == 0
    assert fan._pwm_pin.value() == 0
    print('PUSH_PULL_CONFIG_PASS GP18 freq=%d duty_u16=%d level=%d' %
          (fan._pwm.freq(), fan._pwm.duty_u16(), fan._pwm_pin.value()))
    print('TACH_INPUT', fan._tach)
finally:
    fan.close()
assert fan._pwm_pin.value() == 0
print('PUSH_PULL_CLOSE_PASS GP18 LOW')
