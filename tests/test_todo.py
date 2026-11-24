"""Tests for the parts of todo.pyw that don't need a window.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

# todo.pyw has no .py suffix, so load it with an explicit source loader.
_path = Path(__file__).resolve().parents[1] / "todo.pyw"
_loader = importlib.machinery.SourceFileLoader("todo", str(_path))
todo = importlib.util.module_from_spec(importlib.util.spec_from_loader("todo", _loader))
_loader.exec_module(todo)


def stamp(*args):
    return datetime(*args, 12).timestamp()  # midday, clear of any DST edge


class ParseTest(unittest.TestCase):
    def test_priority_title_repeat(self):
        self.assertEqual(todo.parse("p1 pay rent /monthly"), (1, "pay rent", "monthly"))

    def test_priority_as_last_word(self):
        self.assertEqual(todo.parse("pay rent P2"), (2, "pay rent", None))

    def test_priority_mid_title_is_text(self):
        self.assertEqual(todo.parse("call p2 people"), (None, "call p2 people", None))

    def test_short_and_mixed_case_repeat(self):
        self.assertEqual(todo.parse("/D water plants"), (None, "water plants", "daily"))
        self.assertEqual(todo.parse("bins /w"), (None, "bins", "weekly"))

    def test_whitespace_collapses(self):
        self.assertEqual(todo.parse("  a \t b\nc  "), (None, "a b c", None))

    def test_empty(self):
        self.assertEqual(todo.parse(""), (None, "", None))
        self.assertEqual(todo.parse("p3"), (3, "", None))


class NextDueTest(unittest.TestCase):
    def test_daily_and_weekly(self):
        self.assertEqual(todo.next_due(stamp(2026, 3, 31), "daily"), "2026-04-01")
        self.assertEqual(todo.next_due(stamp(2026, 12, 28), "weekly"), "2027-01-04")

    def test_monthly_clamps_to_month_end(self):
        self.assertEqual(todo.next_due(stamp(2026, 1, 31), "monthly"), "2026-02-28")
        self.assertEqual(todo.next_due(stamp(2028, 1, 31), "monthly"), "2028-02-29")

    def test_monthly_rolls_year(self):
        self.assertEqual(todo.next_due(stamp(2026, 12, 15), "monthly"), "2027-01-15")
        self.assertEqual(todo.next_due(stamp(2026, 11, 30), "monthly"), "2026-12-30")


class SmallHelpersTest(unittest.TestCase):
    def test_is_waiting(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertTrue(todo.is_waiting({"due": tomorrow}))
        self.assertFalse(todo.is_waiting({"due": date.today().isoformat()}))
        self.assertFalse(todo.is_waiting({"due": None}))

    def test_clip(self):
        self.assertEqual(todo.clip("hello", 5), "hello")
        self.assertEqual(todo.clip("hello world", 5), "hell…")

    def test_pri_tag_caps_at_last_colour(self):
        self.assertEqual(todo.pri_tag(None), "")
        self.assertEqual(todo.pri_tag(2), "p2")
        self.assertEqual(todo.pri_tag(99), f"p{len(todo.PRI) - 1}")


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.data = Path(self.dir.name) / "data.json"
        self._saved, todo.DATA = todo.DATA, self.data
        self.addCleanup(setattr, todo, "DATA", self._saved)

    def write(self, obj):
        self.data.write_text(obj if isinstance(obj, str) else json.dumps(obj), "utf-8")

    def assertMovedAside(self, content):
        data, msg = todo.load()
        self.assertEqual(data["todos"], [])
        self.assertFalse(self.data.exists())
        kept = list(Path(self.dir.name).glob("data.bad-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text("utf-8"), content)
        self.assertIn("moved it aside", msg)

    def test_missing_file_is_empty(self):
        data, msg = todo.load()
        self.assertEqual((data["todos"], data["done"], data["pos"], msg), ([], [], None, ""))
        self.assertEqual(data["settings"], todo.DEFAULTS)

    def test_round_trip_is_unchanged(self):
        saved = {
            "todos": [{"title": "pay rent", "pri": 1, "repeat": "monthly", "done_at": None,
                       "due": "2026-12-01", "created": 1.5}],
            "done": [{"title": "file taxes", "pri": None, "done_at": 2.0}],
            "settings": {**todo.DEFAULTS, "theme": "amber"},
            "pos": [10, 20],
        }
        self.write(saved)
        self.assertEqual(todo.load(), (saved, ""))

    def test_corrupt_json_is_kept(self):
        self.write('{"todos": [')
        self.assertMovedAside('{"todos": [')

    def test_wrong_shapes_are_kept(self):
        for bad in ([], {"todos": {}}, {"done": ""}, {"todos": ["x"]},{"todos": [{"title": 3}]},
                    {"todos": [{"title": "a", "due": "someday"}]}, {"done": [{"title": "a"}]},
                    {"settings": []}):
            with self.subTest(bad=bad):
                self.write(bad)
                self.assertMovedAside(json.dumps(bad))
                for f in Path(self.dir.name).glob("*.bad-*.json"):
                    f.unlink()

    def test_old_todos_get_defaults(self):
        self.write({"todos": [{"title": "a\nb"}]})
        data, _ = todo.load()
        self.assertEqual(data["todos"], [{**todo.TODO_FIELDS, "title": "a b"}])

    def test_bad_settings_and_pos_fall_back(self):
        self.write({"settings": {"theme": "blue", "font_size": True, "width": 44, "extra": 1},
                    "pos": [1, "2"], "future": "kept"})
        data, msg = todo.load()
        self.assertEqual(msg, "")
        self.assertEqual(data["settings"], {**todo.DEFAULTS, "extra": 1})
        self.assertIsNone(data["pos"])
        self.assertEqual(data["future"], "kept")


if __name__ == "__main__":
    unittest.main()
