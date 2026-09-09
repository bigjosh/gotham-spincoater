import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'device' / 'fan_settings.py'
SPEC = importlib.util.spec_from_file_location('fan_settings_under_test', PATH)
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


class FanSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'fans.json'

    def tearDown(self):
        self.folder.cleanup()

    def store(self, defaults=(0, 1, 2, 3), available=(0, 1, 2, 3)):
        return settings.FanSettings(defaults, available, path=str(self.path))

    def write(self, channels, suffix=''):
        Path(str(self.path) + suffix).write_text(
            json.dumps({'version': 1, 'enabled': channels}), encoding='utf8')

    def test_absent_files_use_defaults_without_writing(self):
        store = self.store()
        self.assertEqual(store.load(), (0, 1, 2, 3))
        self.assertEqual(store.source, 'defaults')
        self.assertIsNone(store.load_error)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_all_off_round_trip_is_not_replaced_by_defaults(self):
        store = self.store()
        store.load()
        store.save(())
        self.assertEqual(self.store().load(), ())
        self.assertEqual(json.loads(self.path.read_text()), {'version': 1, 'enabled': []})

    def test_availability_mask_never_autoenables_other_channels(self):
        self.write([4, 5])
        store = self.store()
        self.assertEqual(store.load(), ())
        self.assertIn('Unavailable', store.load_error)
        self.assertEqual(self.store(available=tuple(range(6))).load(), (4, 5))

    def test_corrupt_primary_recovers_last_selection_from_backup(self):
        store = self.store()
        store.save((0, 2))
        store.save((3,))
        self.path.write_text('{broken', encoding='utf8')
        self.assertEqual(store.load(), (0, 2))
        self.assertEqual(store.source, 'backup')

    def test_corrupt_files_fall_back_all_off_instead_of_defaults(self):
        for suffix in ('', '.bak'):
            Path(str(self.path) + suffix).write_text('bad', encoding='utf8')
        store = self.store()
        self.assertEqual(store.load(), ())
        self.assertEqual(store.source, 'all_off')
        self.assertIsNotNone(store.load_error)

    def test_missing_primary_invalid_backup_also_chooses_all_off(self):
        Path(str(self.path) + '.bak').write_text('bad', encoding='utf8')
        self.assertEqual(self.store().load(), ())

    def test_oversized_file_uses_valid_backup(self):
        self.path.write_text(' ' * 1025, encoding='utf8')
        self.write([2], '.bak')
        self.assertEqual(self.store().load(), (2,))

    def test_documents_reject_duplicates_boolean_unknown_fields_and_channels(self):
        for channels in ([0, 0], [True], [-1], [6], ['0'], None, '0'):
            with self.subTest(channels=channels):
                with self.assertRaises(ValueError):
                    settings.validate_document({'version': 1, 'enabled': channels})
        for data in ({'version': True, 'enabled': []}, {'version': 2, 'enabled': []},
                     {'version': 1, 'enabled': [], 'recipe': {}}, {}):
            with self.assertRaises(ValueError):
                settings.validate_document(data)

    def test_unavailable_or_invalid_save_keeps_disk_and_live_selection(self):
        store = self.store()
        store.save((0,))
        before = self.path.read_bytes()
        for bad in ((4,), (0, 0), (False,)):
            with self.assertRaises(ValueError):
                store.save(bad)
            self.assertEqual(store.enabled, (0,))
            self.assertEqual(self.path.read_bytes(), before)

    def test_failed_activation_recovers_prior_selection(self):
        store = self.store()
        store.save((0, 2))
        rename = settings.os.rename
        def fail(source, destination):
            if source == str(self.path) + '.tmp':
                raise OSError('activation failed')
            return rename(source, destination)
        with patch.object(settings.os, 'rename', side_effect=fail):
            with self.assertRaises(OSError):
                store.save((1,))
        self.assertEqual(store.enabled, (0, 2))
        self.assertEqual(self.store().load(), (0, 2))

    def test_first_save_interruption_recovers_even_invalid_file_all_off_baseline(self):
        self.path.write_text('invalid', encoding='utf8')
        store = self.store()
        self.assertEqual(store.load(), ())
        rename = settings.os.rename
        def fail(source, destination):
            if source == str(self.path) + '.tmp':
                raise OSError('activation failed')
            return rename(source, destination)
        with patch.object(settings.os, 'rename', side_effect=fail):
            with self.assertRaises(OSError):
                store.save((1,))
        self.assertEqual(self.store().load(), ())

    def test_failed_save_after_backup_recovery_keeps_only_valid_recovery_file(self):
        self.path.write_text('invalid', encoding='utf8')
        self.write([2], '.bak')
        store = self.store()
        self.assertEqual(store.load(), (2,))
        backup = Path(str(self.path) + '.bak').read_bytes()
        with patch.object(settings.os, 'rename', side_effect=OSError('rename failed')):
            with self.assertRaises(OSError):
                store.save((1,))
        self.assertEqual(Path(str(self.path) + '.bak').read_bytes(), backup)
        self.assertEqual(self.store().load(), (2,))

    def test_sync_failure_after_activation_reverts_to_previous_selection(self):
        store = self.store()
        store.save((2,))
        original = store._sync
        calls = []
        def fail_commit():
            calls.append(True)
            if len(calls) == 4:
                raise OSError('commit flush failed')
            original()
        with patch.object(store, '_sync', side_effect=fail_commit):
            with self.assertRaises(OSError):
                store.save((1,))
        self.assertEqual(store.enabled, (2,))
        self.assertFalse(store.uncertain)
        self.assertEqual(self.store().load(), (2,))

    def test_failed_revert_marks_storage_outcome_uncertain(self):
        store = self.store()
        store.save((2,))
        original_sync, original_remove = store._sync, store._remove
        sync_calls, removals = [], []
        def fail_commit():
            sync_calls.append(True)
            if len(sync_calls) == 4:
                raise OSError('commit flush failed')
            original_sync()
        def fail_revert(path):
            if path == str(self.path):
                removals.append(True)
                if len(removals) == 2:
                    raise OSError('cannot revert')
            original_remove(path)
        with patch.object(store, '_sync', side_effect=fail_commit), \
                patch.object(store, '_remove', side_effect=fail_revert):
            with self.assertRaises(OSError):
                store.save((1,))
        self.assertTrue(store.uncertain)
        self.assertEqual(store.enabled, (2,))


if __name__ == '__main__':
    unittest.main()
