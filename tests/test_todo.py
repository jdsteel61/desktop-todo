"""Tests for todo.pyw. Only LayoutTest opens a window (briefly, and on Windows only).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import time
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

    def test_clip_by_display_width(self):
        def wide(s):  # CJK counts as two columns
            return sum(2 if ord(c) >= 0x3000 else 1 for c in s)

        self.assertEqual(todo.clip("日本語です", 10, wide), "日本語です")
        self.assertEqual(todo.clip("日本語です", 6, wide), "日本…")
        self.assertEqual(todo.clip("ab日本", 4, wide), "ab…")

    def test_pri_tag_caps_at_last_colour(self):
        self.assertEqual(todo.pri_tag(None), "")
        self.assertEqual(todo.pri_tag(2), "p2")
        self.assertEqual(todo.pri_tag(99), f"p{len(todo.PRI) - 1}")


def task(title, **fields):
    return {**todo.TODO_FIELDS, "title": title, **fields}


class ArchiveTest(unittest.TestCase):
    def test_moves_finished_and_reschedules_repeats(self):
        hours_ago = time.time() - 7200
        data = {"todos": [task("old", done_at=hours_ago), task("recent", done_at=time.time()),
                          task("daily", repeat="daily", done_at=hours_ago), task("open")], "done": []}
        self.assertTrue(todo.archive(data, linger=60))
        self.assertEqual([t["title"] for t in data["todos"]], ["recent", "daily", "open"])
        self.assertEqual(sorted(x["title"] for x in data["done"]), ["daily", "old"])
        self.assertIsNone(data["todos"][1]["done_at"])
        self.assertTrue(todo.is_waiting(data["todos"][1]))
        self.assertFalse(todo.archive(data, linger=60))
        self.assertTrue(todo.archive(data, linger=60, force=True))
        self.assertEqual([t["title"] for t in data["todos"]], ["daily", "open"])

    def test_done_log_keeps_only_the_newest(self):
        n = todo.DONE_KEEP + 50
        data = {"todos": [], "done": [{"title": str(i), "pri": None, "done_at": float(i)} for i in range(n)]}
        self.assertTrue(todo.archive(data, linger=60))
        self.assertEqual(len(data["done"]), todo.DONE_KEEP)
        self.assertEqual((data["done"][0]["done_at"], data["done"][-1]["done_at"]), (n - 1, n - todo.DONE_KEEP))
        self.assertFalse(todo.archive(data, linger=60))

    def test_done_log_stays_bounded_over_time(self):
        data = {"todos": [], "done": []}
        for i in range(todo.DONE_KEEP * 3):
            data["todos"].append(task(f"t{i}", done_at=float(i)))
            todo.archive(data, linger=0)
        self.assertEqual(len(data["done"]), todo.DONE_KEEP)
        self.assertEqual(data["done"][0]["title"], f"t{todo.DONE_KEEP * 3 - 1}")


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



@unittest.skipUnless(sys.platform == "win32", "the window is Windows-only")
class LayoutTest(unittest.TestCase):
    """Every line must end exactly at the window's right edge, whatever the text, in every view."""

    TITLES = ["plain ascii", "日本語のタスクを書く", "party 🎉 prep ✅", "x" * 200,
              "a long title that ends in emoji so it gets clipped 🎉🎉🎉"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._saved, todo.DATA = todo.DATA, Path(tmp.name) / "data.json"
        self.addCleanup(setattr, todo, "DATA", self._saved)
        soon = (date.today() + timedelta(days=3)).isoformat()
        todos = [task(t, pri=i, created=float(i)) for i, t in enumerate(self.TITLES)]
        todos += [task("週次 review 🗓", repeat="weekly", due=soon), task("done 🎉 日本", done_at=time.time())]
        done = [{"title": t, "pri": None, "done_at": time.time() - 60} for t in self.TITLES]
        todo.DATA.write_text(json.dumps({"todos": todos, "done": done}), "utf-8")
        self.app = todo.App()  # default settings: start at login is off, so the registry is untouched
        self.addCleanup(self.app.root.destroy)
        self.app.root.deiconify()

    def assertAligned(self):
        app = self.app
        app.render()
        app.root.update()
        t, edge = app.text, app.cw * app.s["width"]
        lines = int(t.index("end -1c").split(".")[0])
        for n in range(1, lines + 1):
            box = t.bbox(f"{n}.end -1c")  # the border character; None if pushed out of view
            self.assertIsNotNone(box, f"line {n} overflows")
            self.assertEqual(box[0] + box[2], edge, f"line {n}: {t.get(f'{n}.0', f'{n}.end')!r}")

    def test_every_view(self):
        for view in ("list", "log", "settings", "help"):
            with self.subTest(view=view):
                self.app.switch(view)
                self.assertAligned()

    def test_other_sizes_and_editing(self):
        self.app.s.update(font_size=16, width=36)
        self.app.apply()
        self.assertAligned()
        self.app.begin_input(self.app.rows()[1])
        self.assertAligned()


if __name__ == "__main__":
    unittest.main()
