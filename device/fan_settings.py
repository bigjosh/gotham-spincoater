"""Small, independent persistent fan selection; recipes never change it.

Missing primary and backup use the installation defaults. If either file
exists but neither validates, select no fans until the operator chooses them.
The hardware-availability mask always wins over saved selections.
"""
import json
import os


def validate_channels(channels):
    if (not isinstance(channels, (list, tuple))
            or any(type(i) is not int or not 0 <= i < 6 for i in channels)
            or len(set(channels)) != len(channels)):
        raise ValueError('Fan channels must be unique integers from 0 to 5')
    return tuple(sorted(channels))


def validate_document(data):
    if (not isinstance(data, dict) or set(data) != {'version', 'enabled'}
            or type(data['version']) is not int or data['version'] != 1
            or not isinstance(data['enabled'], list)):
        raise ValueError('Invalid fan settings document')
    return validate_channels(data['enabled'])


class FanSettings:
    def __init__(self, defaults=(0,), available=(0, 1, 2, 3), path='fans.json'):
        self.path = path
        self.available = validate_channels(available)
        self.defaults = self._mask(validate_channels(defaults))
        self.enabled = self.defaults
        self.source = 'defaults'
        self.load_error = None
        self.uncertain = False

    def _mask(self, channels):
        return tuple(i for i in channels if i in self.available)

    def _read(self, path):
        with open(path, 'r') as stream:
            text = stream.read(1025)
        if len(text) > 1024:
            raise ValueError('Fan settings file is too large')
        return validate_document(json.loads(text))

    @staticmethod
    def _missing(error):
        return isinstance(error, OSError) and bool(error.args) and error.args[0] == 2

    def load(self):
        failures = []
        missing = 0
        for path, source in ((self.path, 'primary'), (self.path + '.bak', 'backup')):
            try:
                saved = self._read(path)
                self.enabled = self._mask(saved)
                self.source = source
                if saved != self.enabled:
                    failures.append('Unavailable fan channels were disabled')
                self.load_error = '; '.join(failures) or None
                return self.enabled
            except (OSError, ValueError, TypeError) as error:
                if self._missing(error):
                    missing += 1
                else:
                    failures.append(source + ': ' + str(error))
        self.enabled = self.defaults if missing == 2 else ()
        self.source = 'defaults' if missing == 2 else 'all_off'
        self.load_error = '; '.join(failures) or None
        return self.enabled

    @staticmethod
    def _sync():
        if hasattr(os, 'sync'):
            os.sync()

    def _write(self, path, channels):
        with open(path, 'w') as stream:
            json.dump({'version': 1, 'enabled': list(channels)}, stream)
            stream.flush()
        self._sync()
        if self._read(path) != channels:
            raise OSError('Fan settings verification failed')

    def _remove(self, path):
        try:
            os.remove(path)
        except OSError as error:
            if not self._missing(error):
                raise

    def save(self, channels):
        candidate = validate_channels(channels)
        if any(i not in self.available for i in candidate):
            raise ValueError('Fan GPIOs are unavailable until the kit links are disconnected')
        temporary, backup = self.path + '.tmp', self.path + '.bak'
        backup_temporary = backup + '.tmp'
        # Stage and validate both the new selection and recovery selection
        # before touching the primary. This also gives a first-ever save a
        # recoverable baseline if activation is interrupted.
        self._write(temporary, candidate)
        try:
            recovery_matches = self._mask(self._read(backup)) == self.enabled
        except (OSError, ValueError, TypeError):
            recovery_matches = False
        if not recovery_matches:
            # Never replace the sole valid recovery file when boot recovered
            # from it. If a new backup is needed, the primary still represents
            # the current selection (or the conservative all-off/default state).
            self._write(backup_temporary, self.enabled)
            self._remove(backup)
            os.rename(backup_temporary, backup)
            self._sync()
        activated = False
        try:
            self._remove(self.path)
            os.rename(temporary, self.path)
            activated = True
            self._sync()
        except OSError:
            if activated:
                # A reported failed save must not leave the candidate active
                # on disk while the old selection is still active in RAM.
                try:
                    self._remove(self.path)
                    self._sync()
                except OSError:
                    self.uncertain = True
            raise
        self.enabled = candidate
        self.source, self.load_error = 'primary', None
        self.uncertain = False
        return self.enabled
