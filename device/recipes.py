"""Validated recipe data and replace-on-success JSON persistence.

No file is written until save() is called. Keep saves on the idle UI core.
"""

import json
import os


MAX_STEPS = 9
MAX_RPM = 4000
MAX_SECONDS = 3600
DEFAULT_SETTINGS = {
    "max_power_per_s": 10,
    "tolerance_rpm": 50,
    "rpm_warning_percent": 5,
    "rpm_warning_delay_s": 2,
}
LEGACY_SETTINGS = ("max_power_per_s", "tolerance_rpm", "settle_s", "reach_timeout_s")
DEFAULT_DATA = {
    "version": 1,
    "selected": "Default",
    "settings": DEFAULT_SETTINGS,
    "profiles": [{"name": "Default", "steps": [
        {"rpm": 0, "slew_s": 0, "dwell_s": 0},
        {"rpm": 3000, "slew_s": 15, "dwell_s": 30},
        {"rpm": 0, "slew_s": 15, "dwell_s": 0},
    ]}],
}


def _copy(value):
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(label + " has unexpected or missing fields")


def _number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " must be a number")
    if not low <= value <= high:
        raise ValueError(label + " must be between %s and %s" % (low, high))
    return value


def validate_settings(settings):
    # Migrate the exact previous schema in memory. Existing recipes and
    # their backup stay untouched until the user explicitly saves settings.
    if isinstance(settings, dict) and set(settings) == set(LEGACY_SETTINGS):
        _number(settings["settle_s"], 0.05, 10, "settle_s")
        _number(settings["reach_timeout_s"], 1, 120, "reach_timeout_s")
        settings = {
            "max_power_per_s": settings["max_power_per_s"],
            "tolerance_rpm": settings["tolerance_rpm"],
            "rpm_warning_percent": DEFAULT_SETTINGS["rpm_warning_percent"],
            "rpm_warning_delay_s": DEFAULT_SETTINGS["rpm_warning_delay_s"],
        }
    _keys(settings, DEFAULT_SETTINGS, "settings")
    ranges = {
        "max_power_per_s": (0.1, 100),
        "tolerance_rpm": (1, 500),
        "rpm_warning_percent": (0.1, 100),
        "rpm_warning_delay_s": (0, 120),
    }
    result = {}
    for key, bounds in ranges.items():
        result[key] = _number(settings[key], bounds[0], bounds[1], key)
    return result


def validate_profile(profile):
    _keys(profile, ("name", "steps"), "profile")
    name = profile["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 40:
        raise ValueError("profile name must contain 1-40 characters")
    if name != name.strip() or any(ord(char) < 32 for char in name):
        raise ValueError("profile name must not contain outer whitespace or controls")
    steps = profile["steps"]
    if not isinstance(steps, list) or not 2 <= len(steps) <= MAX_STEPS:
        raise ValueError("a profile needs 2-9 steps, including zero endpoints")
    result = []
    for step in steps:
        _keys(step, ("rpm", "slew_s", "dwell_s"), "step")
        result.append({
            "rpm": _number(step["rpm"], 0, MAX_RPM, "rpm"),
            "slew_s": _number(step["slew_s"], 0, MAX_SECONDS, "slew_s"),
            "dwell_s": _number(step["dwell_s"], 0, MAX_SECONDS, "dwell_s"),
        })
    if result[0]["rpm"] != 0 or result[-1]["rpm"] != 0:
        raise ValueError("first and last step must command zero RPM")
    return {"name": name, "steps": result}


def validate_document(data):
    _keys(data, ("version", "selected", "settings", "profiles"), "recipe file")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported recipe version")
    profiles = data["profiles"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 20:
        raise ValueError("recipe file needs 1-20 profiles")
    profiles = [validate_profile(profile) for profile in profiles]
    names = [profile["name"] for profile in profiles]
    if len(set(names)) != len(names):
        raise ValueError("profile names must be unique")
    if data["selected"] not in names:
        raise ValueError("selected profile does not exist")
    return {"version": 1, "selected": data["selected"],
            "settings": validate_settings(data["settings"]), "profiles": profiles}


class RecipeBook:
    def __init__(self, path="recipes.json"):
        self.path = path
        self.data = _copy(DEFAULT_DATA)
        self.source = "defaults"
        self.load_error = None

    def _read(self, path):
        with open(path, "r") as stream:
            # Bound allocations from a damaged or externally supplied file.
            text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError("recipe file is too large")
        return validate_document(json.loads(text))

    def load(self):
        failures = []
        for path, source in ((self.path, "primary"), (self.path + ".bak", "backup")):
            try:
                self.data = self._read(path)
                self.source = source
                self.load_error = "; ".join(failures) or None
                return self.data
            except (OSError, ValueError, TypeError) as exc:
                failures.append(source + ": " + str(exc))
        self.data = _copy(DEFAULT_DATA)
        self.source = "defaults"
        self.load_error = "; ".join(failures)
        return self.data

    def selectedprofile(self):
        data = validate_document(self.data)
        for profile in data["profiles"]:
            if profile["name"] == data["selected"]:
                return _copy(profile)

    def settings(self):
        return validate_settings(self.data["settings"])

    def save(self, data=None):
        candidate = validate_document(self.data if data is None else data)
        temporary = self.path + ".tmp"
        backup = self.path + ".bak"
        with open(temporary, "w") as stream:
            json.dump(candidate, stream)
            stream.flush()
        if hasattr(os, "sync"):
            os.sync()
        # Validate the complete staged file before changing the old primary.
        self._read(temporary)
        try:
            self._read(self.path)
            valid_primary = True
        except (OSError, ValueError, TypeError):
            valid_primary = False
        if valid_primary:
            try:
                os.remove(backup)
            except OSError:
                pass
            os.rename(self.path, backup)
        else:
            try:
                os.remove(self.path)
            except OSError:
                pass
        # If interrupted before this rename, load() recovers the valid backup.
        os.rename(temporary, self.path)
        if hasattr(os, "sync"):
            os.sync()
        self.data = candidate
        self.source = "primary"
        self.load_error = None
        return _copy(candidate)
