"""Rolling window of complete tach periods, independent of motor state."""

from array import array
from machine import disable_irq, enable_irq
from time import ticks_diff, ticks_us


class PeriodMeasurements:
    """Average full-cycle durations, then convert their mean to RPM.

    record() accepts integer microseconds and performs small-integer work only.
    Zero is a reset sentinel. Two accepted periods establish a valid reading.
    Pulse totals count accepted complete periods, excluding reset/timeout data.
    Call sample regularly to latch timeouts and extend the bounded IRQ counter.
    The rig's control core owns record/sample; legacy IRQ callers are supported.
    RUN, STOP, fan selection, and display thresholds never change this window.
    """

    _MASK = 0x1FFFFFFF

    def __init__(self, pulses_per_rev=2, window=8, timeout_ms=1500):
        if pulses_per_rev <= 0:
            raise ValueError("pulses_per_rev must be positive")
        if window < 2 or int(window) != window:
            raise ValueError("window must be an integer of at least two")
        if timeout_ms < 1 or int(timeout_ms) != timeout_ms:
            raise ValueError("timeout_ms must be a positive integer")
        self._window = int(window)
        self._timeout_us = int(timeout_ms) * 1000
        if self._timeout_us * self._window > self._MASK:
            raise ValueError("window and timeout exceed IRQ small-integer limit")
        self._ppr = pulses_per_rev
        self._periods = array("I", [0] * self._window)
        self._sum_us = 0
        self._size = 0
        self._head = 0
        self._last_us = 0
        self._has_time = False
        self._restart_needed = False
        self._count = 0
        self._accounted_count = 0
        self._total = 0
        self._peak_rpm = 0.0

    def record(self, period_us):
        """Record an integer full-cycle duration; safe inside a hard IRQ.

        Invalid durations clear the window. A zero sentinel explicitly clears
        timing history because the PIO producer has discarded its old baseline.
        After a wall-time timeout, discard the first returning duration so a
        possibly stale cycle cannot bridge the stopped interval.
        """
        if period_us < 1 or period_us > self._timeout_us:
            self._sum_us = 0
            self._size = 0
            self._head = 0
            self._has_time = False
            self._restart_needed = False
            return

        now = ticks_us()
        gap = ticks_diff(now, self._last_us) if self._has_time else 0
        if self._restart_needed or gap < 0 or gap >= self._timeout_us:
            self._sum_us = 0
            self._size = 0
            self._head = 0
            self._last_us = now
            self._has_time = True
            self._restart_needed = False
            return

        head = self._head
        if self._size == self._window:
            self._sum_us -= self._periods[head]
        else:
            self._size += 1
        self._periods[head] = period_us
        self._sum_us += period_us
        head += 1
        self._head = 0 if head == self._window else head
        self._count = (self._count + 1) & self._MASK
        self._last_us = now
        self._has_time = True

    def sample(self):
        """Return RPM, pulse Hz, pulse total, age, validity, peak and mean period."""
        state = disable_irq()
        now = ticks_us()
        age_us = ticks_diff(now, self._last_us) if self._has_time else None
        if self._has_time and (age_us < 0 or age_us >= self._timeout_us):
            self._sum_us = 0
            self._size = 0
            self._head = 0
            self._restart_needed = True
        size = self._size
        sum_us = self._sum_us
        count = self._count
        restart_needed = self._restart_needed
        enable_irq(state)

        self._total += (count - self._accounted_count) & self._MASK
        self._accounted_count = count
        valid = size >= 2 and not restart_needed
        period_us = sum_us / size if size else 0.0
        hz = 1000000.0 / period_us if valid else 0.0
        rpm = 60000000.0 / (period_us * self._ppr) if valid else 0.0
        if valid and rpm > self._peak_rpm:
            self._peak_rpm = rpm
        return {
            "rpm": rpm,
            "hz": hz,
            "pulses": self._total,
            "age_ms": max(0, age_us // 1000) if age_us is not None else None,
            "valid": valid,
            "peak_rpm": self._peak_rpm,
            "period_us": period_us,
            "samples": size,
        }
