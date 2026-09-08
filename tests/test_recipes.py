import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "device" / "recipes.py"
SPEC = importlib.util.spec_from_file_location("recipes", PATH)
recipes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipes)


def defaults():
    return recipes.validate_document(recipes.DEFAULT_DATA)


class RecipeTests(unittest.TestCase):
    def test_defaults_and_detached_run_copy(self):
        book = recipes.RecipeBook()
        profile = book.selectedprofile()
        profile["steps"][1]["rpm"] = 2000
        self.assertEqual(book.selectedprofile()["steps"][1]["rpm"], 3000)
        settings = book.settings()
        settings["max_power_per_s"] = 20
        self.assertEqual(book.settings()["max_power_per_s"], 10)

    def test_rejects_nonfinite_bool_and_out_of_range_values(self):
        for value in (float("nan"), float("inf"), -1, 4001, True, "3000"):
            with self.subTest(value=value):
                data = defaults()
                data["profiles"][0]["steps"][1]["rpm"] = value
                with self.assertRaises(ValueError):
                    recipes.validate_document(data)
        for key in ("slew_s", "dwell_s"):
            data = defaults()
            data["profiles"][0]["steps"][1][key] = 3601
            with self.assertRaises(ValueError):
                recipes.validate_document(data)

    def test_endpoints_and_step_count(self):
        for endpoint in (0, -1):
            data = defaults()
            data["profiles"][0]["steps"][endpoint]["rpm"] = 1
            with self.assertRaises(ValueError):
                recipes.validate_document(data)
        data = defaults()
        data["profiles"][0]["steps"] = [{"rpm": 0, "slew_s": 0, "dwell_s": 0}] * 9
        self.assertEqual(len(recipes.validate_document(data)["profiles"][0]["steps"]), 9)
        data["profiles"][0]["steps"].append(data["profiles"][0]["steps"][0])
        with self.assertRaises(ValueError):
            recipes.validate_document(data)

    def test_rejects_duplicate_names_missing_selection_and_unknown_fields(self):
        data = defaults()
        data["profiles"].append(data["profiles"][0])
        with self.assertRaises(ValueError):
            recipes.validate_document(data)
        data = defaults()
        data["selected"] = "Missing"
        with self.assertRaises(ValueError):
            recipes.validate_document(data)
        data = defaults()
        data["settings"]["typo"] = 1
        with self.assertRaises(ValueError):
            recipes.validate_document(data)

    def test_settings_bounds_and_version(self):
        for key in recipes.DEFAULT_SETTINGS:
            data = defaults()
            data["settings"][key] = 0
            with self.assertRaises(ValueError):
                recipes.validate_document(data)
        data = defaults()
        data["version"] = True
        with self.assertRaises(ValueError):
            recipes.validate_document(data)

    def test_load_does_not_write_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "recipes.json")
            book = recipes.RecipeBook(path)
            book.load()
            self.assertEqual(book.source, "defaults")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_save_round_trip_and_backup_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "recipes.json")
            book = recipes.RecipeBook(path)
            book.save()
            second = defaults()
            second["settings"]["max_power_per_s"] = 20
            book.save(second)
            loaded = recipes.RecipeBook(path)
            self.assertEqual(loaded.load()["settings"]["max_power_per_s"], 20)
            Path(path).write_text("{broken", encoding="utf-8")
            self.assertEqual(loaded.load()["settings"]["max_power_per_s"], 10)
            self.assertEqual(loaded.source, "backup")

    def test_invalid_save_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "recipes.json")
            book = recipes.RecipeBook(path)
            book.save()
            before = Path(path).read_bytes()
            data = defaults()
            data["profiles"][0]["steps"][0]["rpm"] = 10
            with self.assertRaises(ValueError):
                book.save(data)
            self.assertEqual(Path(path).read_bytes(), before)

    def test_interrupted_activation_recovers_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "recipes.json")
            book = recipes.RecipeBook(path)
            book.save()
            original = recipes.os.rename

            def fail_activation(source, destination):
                if source.endswith(".tmp"):
                    raise OSError("simulated interrupted activation")
                return original(source, destination)

            with patch.object(recipes.os, "rename", side_effect=fail_activation):
                with self.assertRaises(OSError):
                    book.save()
            loaded = recipes.RecipeBook(path)
            self.assertEqual(loaded.load(), defaults())
            self.assertEqual(loaded.source, "backup")

    def test_large_or_invalid_primary_uses_valid_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "recipes.json")
            Path(path).write_text(" " * 65537, encoding="utf-8")
            Path(path + ".bak").write_text(json.dumps(defaults()), encoding="utf-8")
            book = recipes.RecipeBook(path)
            self.assertEqual(book.load(), defaults())
            self.assertEqual(book.source, "backup")


if __name__ == "__main__":
    unittest.main()
