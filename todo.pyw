"""desktop-todo: a tiny TUI-styled to-do list that sits on the Windows desktop.

Standard library only. Double-click todo.pyw to run; press ? in the app for keys.
"""
import calendar
import ctypes
import json
import math
import os
import re
import sys
import time
import tkinter as tk
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont

APP = Path(__file__).resolve()
DATA = Path(os.environ.get("TODO_DATA") or APP.with_name("data.json"))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ERROR_ALREADY_EXISTS = 183
MAX_ROWS = 20
DONE_KEEP = 500  # the done log keeps this many of the newest entries
TICK_MS = 20_000
ENTRY = "\0entry"  # marks where the text input sits in a rendered line
GAP = "\t"  # splits a rendered line: what follows is right-aligned against the window edge

# Checkbox colour by priority: P0, P1, ... anything past the end uses the last.
PRI = ["#ff5555", "#ff9e3b", "#f1d263", "#7bd88f", "#5ccfe6", "#7aa2f7", "#bb9af7", "#8a8f98"]
THEMES = {  # background, text, dim, selected row
    "dark": ("#0d1117", "#c9d1d9", "#5c6570", "#1f2937"),
    "amber": ("#120c00", "#ffb000", "#7a5600", "#2e2000"),
    "green": ("#00140a", "#39ff88", "#1a7a45", "#00301a"),
}
REPEATS = [None, "daily", "weekly", "monthly"]
REPEAT_TOKENS = {f"/{name}": r for r in REPEATS[1:] for name in (r, r[0])}

DEFAULTS = dict(theme="dark", font_size=11, width=44, on_top=False, linger=60, autostart=False)
OPTIONS = [  # setting, label, choices
    ("theme", "theme", list(THEMES)),
    ("font_size", "font size", list(range(8, 21))),
    ("width", "width", list(range(30, 82, 2))),
    ("on_top", "always on top", [False, True]),
    ("linger", "clear done after", [15, 30, 60, 120, 240, 480]),
    ("autostart", "start at login", [False, True]),
]
HELP = [
    ("a", "add: p1 title /daily"),
    ("f2 e", "rename (or click twice)"),
    ("space x", "done / undo"),
    ("0-9", "priority (again: clear)"),
    ("+ -", "step priority"),
    ("r", "cycle repeat"),
    ("dd", "delete"),
    ("c", "clear done now"),
    ("j k", "move"),
    ("tab", "done list"),
    ("s", "settings"),
    ("q", "quit"),
    ("drag top", "move window"),
]
FOOTERS = {
    "list": "a add  0-9 pri  spc done  s set  ? keys",
    "log": "tab back",
    "settings": "left/right change  esc back",
    "help": "esc back",
}


TODO_FIELDS = dict(title="", pri=None, repeat=None, done_at=None, due=None, created=0.0)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_pri(x):
    return x is None or (isinstance(x, int) and not isinstance(x, bool) and x >= 0)


def check(raw):
    """Validate parsed file contents, filling in defaults. Raises for anything unusable."""
    if not isinstance(raw, dict):
        raise ValueError("not a todo file")
    data = {"todos": [], "done": [], "settings": {}, "pos": None, **raw}
    if not (isinstance(data["todos"], list) and isinstance(data["done"], list)):
        raise ValueError("bad lists")
    todos = [{**TODO_FIELDS, **t} for t in data["todos"]]
    done = [{"pri": None, **x} for x in data["done"]]
    for t in todos:
        if not (isinstance(t["title"], str) and is_pri(t["pri"]) and t["repeat"] in REPEATS
                and (t["done_at"] is None or is_num(t["done_at"])) and is_num(t["created"])):
            raise ValueError(f"bad todo: {t!r}")
        if t["due"] is not None:
            date.fromisoformat(t["due"])
    for x in done:
        if not (isinstance(x["title"], str) and is_pri(x["pri"]) and is_num(x["done_at"])):
            raise ValueError(f"bad done entry: {x!r}")
    for x in todos + done:  # a hand-edited newline or tab would break the box drawing
        x["title"] = " ".join(x["title"].split())

    # A bad setting or window position just falls back to its default.
    if not isinstance(data["settings"], dict):
        raise ValueError("bad settings")
    settings = {**DEFAULTS, **data["settings"]}
    for key, _, choices in OPTIONS:
        if type(settings[key]) is not type(DEFAULTS[key]) or settings[key] not in choices:
            settings[key] = DEFAULTS[key]
    pos = data["pos"]
    if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
        pos = None
    return {**data, "todos": todos, "done": done, "settings": settings, "pos": pos}


def load():
    """Read the data file -> (data, message). An unusable file is moved aside, never overwritten."""
    try:
        return check(json.loads(DATA.read_text("utf-8"))), ""
    except FileNotFoundError:
        return check({}), ""
    except (ValueError, TypeError, KeyError):
        kept = DATA.with_suffix(time.strftime(".bad-%Y%m%d-%H%M%S.json"))
        DATA.replace(kept)
        return check({}), f"couldn't read {DATA.name}; moved it aside"


def parse(text):
    """'p1 pay rent /monthly' -> (1, 'pay rent', 'monthly'). Priority is blank unless given."""
    words = text.split()
    repeat = next((REPEAT_TOKENS[w.lower()] for w in words if w.lower() in REPEAT_TOKENS), None)
    words = [w for w in words if w.lower() not in REPEAT_TOKENS]
    pri = None
    for i in (0, -1):  # as the first or last word, so "p2" mid-sentence stays in the title
        if words and re.fullmatch(r"[pP]\d+", words[i]):
            pri = int(words.pop(i)[1:])
            break
    return pri, " ".join(words), repeat


def next_due(done_at, repeat):
    d = date.fromtimestamp(done_at)
    if repeat == "daily":
        d += timedelta(days=1)
    elif repeat == "weekly":
        d += timedelta(days=7)
    else:
        year, month = d.year + d.month // 12, d.month % 12 + 1
        d = date(year, month, min(d.day, calendar.monthrange(year, month)[1]))
    return d.isoformat()


def is_waiting(t):
    """A repeating todo that is hidden from the active list until its next date."""
    return (t["due"] or "") > date.today().isoformat()


def clip(s, n, width=len):
    """Cut s to fit n columns, ending in an ellipsis. width measures a string in columns."""
    if width(s) <= n:
        return s
    lo, hi = 0, len(s)  # binary search for the longest prefix that fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if width(s[:mid] + "…") <= n else (lo, mid - 1)
    return s[:lo] + "…"


def archive(data, linger, force=False):
    """Move todos finished over `linger` minutes ago to the done log. Returns True if data changed."""
    cutoff = time.time() - linger * 60
    todos, done = data["todos"], data["done"]
    finished = [t for t in todos if t["done_at"] and (force or t["done_at"] <= cutoff)]
    for t in finished:
        done.append({"title": t["title"], "pri": t["pri"], "done_at": t["done_at"]})
        if t["repeat"]:
            t["due"], t["done_at"] = next_due(t["done_at"], t["repeat"]), None
        else:
            todos.remove(t)
    if not finished and len(done) <= DONE_KEEP:
        return False
    done.sort(key=lambda x: x["done_at"], reverse=True)
    del done[DONE_KEEP:]  # so neither the log nor the data file grows forever
    return True


def pri_tag(pri):
    return "" if pri is None else f"p{min(pri, len(PRI) - 1)}"


def pri_label(pri):
    return "  " if pri is None else f"P{pri}"


def set_autostart(on):
    """Add or remove the HKCU Run entry that starts this app at login."""
    import winreg  # Windows-only: imported here so the module itself loads anywhere (tests)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            exe = Path(sys.executable).with_name("pythonw.exe")  # no console window
            exe = exe if exe.exists() else Path(sys.executable)
            winreg.SetValueEx(key, "desktop-todo", 0, winreg.REG_SZ, f'"{exe}" "{APP}"')
        else:
            try:
                winreg.DeleteValue(key, "desktop-todo")
            except FileNotFoundError:
                pass


class App:
    def __init__(self):
        self.d, self.msg = load()  # msg: one-off note shown in the footer until the next key
        self.s = self.d["settings"]
        self.view = "list"
        self.cursor = dict.fromkeys(FOOTERS, 0)
        self.top = 0
        self.editing = None  # None, "new", or the todo being edited
        self.armed = False  # first d of dd has been pressed
        self.drag = None
        self.last = None

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        self.root = root = tk.Tk()
        root.title("todo")
        root.overrideredirect(True)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.report_callback_exception = self.on_error
        family = next((f for f in ("Cascadia Mono", "Consolas") if f in tkfont.families()), "Courier New")
        self.font = tkfont.Font(family=family, size=self.s["font_size"])
        self.text = tk.Text(root, font=self.font, wrap="none", bd=0, padx=0, pady=0, height=1,
                            highlightthickness=0, cursor="arrow", takefocus=0, state="disabled")
        self.text.pack()
        self.entry = None

        root.bind("<Key>", self.on_key)
        for seq, fn in (("<Button-1>", self.on_press), ("<Double-Button-1>", self.on_double),
                        ("<B1-Motion>", self.on_motion), ("<ButtonRelease-1>", self.on_release),
                        ("<MouseWheel>", self.on_wheel)):
            self.text.bind(seq, fn)

        if self.s["autostart"]:  # rewrite the entry in case the folder or Python has moved
            self.autostart(True)
        self.sweep()
        self.apply()
        self.show_in_taskbar()
        self.tick()

    def on_error(self, exc, value, tb):
        """Show errors in the footer: pythonw has no console to print them to."""
        if sys.stderr:
            traceback.print_exception(exc, value, tb)
        self.msg = f"error: {value or exc.__name__}"
        if self.editing is None:  # a redraw would destroy the text box
            self.render()

    # --- data ---------------------------------------------------------------

    def save(self):
        """Write atomically, retrying briefly: antivirus and sync tools can hold the file."""
        tmp = DATA.with_suffix(".tmp")
        for _ in range(5):
            try:
                DATA.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(self.d, indent=1, ensure_ascii=False), "utf-8")
                os.replace(tmp, DATA)
                return
            except OSError as e:
                error = e
                time.sleep(0.05)
        self.msg = f"can't save: {error.strerror or error}"

    def autostart(self, on):
        try:
            set_autostart(on)
            return True
        except OSError as e:
            self.msg = f"start at login failed: {e.strerror or e}"
            return False

    def rows(self):
        def key(t):
            waiting = is_waiting(t)
            return waiting, t["due"] if waiting else "", t["pri"] is None, t["pri"] or 0, t["created"]
        return sorted(self.d["todos"], key=key)

    def sweep(self, force=False):
        if archive(self.d, self.s["linger"], force):
            self.save()

    def toggle(self, t):
        if is_waiting(t):  # pull a waiting repeat forward to today
            t["due"] = None
        else:
            t["done_at"] = None if t["done_at"] else time.time()

    def follow(self, t):
        """Keep the cursor on a todo after the list has been reordered."""
        self.idx = next((i for i, x in enumerate(self.rows()) if x is t), self.idx)

    # --- view state ---------------------------------------------------------

    def items(self):
        return {"list": self.rows, "log": lambda: self.d["done"],
                "settings": lambda: OPTIONS, "help": lambda: HELP}[self.view]()

    @property
    def idx(self):
        return self.cursor[self.view]

    @idx.setter
    def idx(self, value):
        self.cursor[self.view] = max(0, min(value, len(self.items()) - 1))

    def switch(self, view):
        self.view, self.top = view, 0

    # --- rendering ----------------------------------------------------------

    def cols(self, s):
        """Width of s in character cells. Emoji and CJK come from fallback fonts and aren't whole cells,
        so lines are laid out by measuring, with a GAP tab stop lining up the right edge exactly."""
        return self.font.measure(s) / self.cw

    def bar(self, left, right, label, tag=""):
        label = clip(label, self.s["width"] - 5, self.cols)
        fill = "─" * int(self.s["width"] - 5 - self.cols(label))
        return [(f"{left}─ ", "dim"), (label, tag), (GAP, ""), (fill + right, "dim")]

    def boxed(self, segs, selected=False):
        """Frame a row in borders. Anything after a GAP sits right-aligned, a space in from the border."""
        if any(s == GAP for s, _ in segs):
            segs = [(" ", ""), *segs, (" ", "")]
        else:
            segs = [(" ", ""), *segs, (GAP, "")]
        if selected:
            segs = [(s, f"{tag} cursor".strip()) for s, tag in segs]
        return [("│", "dim"), *segs, ("│", "dim")]

    def todo_row(self, t):
        waiting = is_waiting(t)
        status = [f"~{t['repeat'][0]}"] if t["repeat"] else []
        if t["done_at"]:
            left = self.s["linger"] * 60 - (time.time() - t["done_at"])
            status.append(f"{max(math.ceil(left / 60), 1)}m")
        elif waiting:
            due = date.fromisoformat(t["due"])
            status.append(due.strftime("%a" if (due - date.today()).days < 7 else "%b %d"))
        status = " ".join(status)
        label = pri_label(t["pri"])
        color = "dim" if waiting else pri_tag(t["pri"])
        room = self.s["width"] - 9 - len(label) - (self.cols(status) + 1 if status else 0)
        title = clip(t["title"], room, self.cols)
        row = [("[x]" if t["done_at"] else "[ ]", color), (" ", ""), (label, color), (" ", ""),
               (title, "struck" if t["done_at"] else "dim" if waiting else "")]
        return [*row, (GAP, ""), (status, "dim")] if status else row

    def log_row(self, x):
        stamp = datetime.fromtimestamp(x["done_at"]).strftime("%b %d %H:%M")
        label = pri_label(x["pri"])
        room = self.s["width"] - 6 - self.cols(stamp) - len(label)
        return [(stamp, "dim"), (" ", ""), (label, pri_tag(x["pri"])), (" ", ""),
                (clip(x["title"], room, self.cols), "")]

    def setting_row(self, option):
        key, label, _ = option
        value = self.s[key]
        if isinstance(value, bool):
            value = "on" if value else "off"
        elif key == "linger":
            value = f"{value // 60}h" if value >= 60 else f"{value}m"
        value = f"< {value} >"
        return [(label, ""), (GAP, ""), (value, "")]

    def help_row(self, row):
        return [(f"{row[0]:<9}", "dim"), (clip(row[1], self.s["width"] - 13), "")]

    def input_prefix(self):
        """What sits left of the text box: a prompt, or the checkbox and priority being renamed."""
        return [("> ", "dim")] if self.editing == "new" else self.todo_row(self.editing)[:4]

    def input_row(self):
        prefix = [(s, f"{tag} cursor".strip()) for s, tag in self.input_prefix()]
        return [("│", "dim"), (" ", "cursor"), *prefix, (ENTRY, ""), (" ", "cursor"), ("│", "dim")]

    def build(self):
        items = self.items()
        self.idx = self.idx
        i = self.idx
        if i < self.top:
            self.top = i
        elif i >= self.top + MAX_ROWS:
            self.top = i - MAX_ROWS + 1
        self.top = max(0, min(self.top, len(items) - MAX_ROWS))

        row = {"list": self.todo_row, "log": self.log_row,
               "settings": self.setting_row, "help": self.help_row}[self.view]
        body = [self.input_row() if x is self.editing
                else self.boxed(row(x), self.top + n == i and self.view != "help")
                for n, x in enumerate(items[self.top:self.top + MAX_ROWS])]
        if not items:
            empty = {"list": "nothing to do - press a", "log": "nothing finished yet"}[self.view]
            body = [self.boxed([(empty, "dim")])]

        titles = {
            "list": f"todo {sum(not t['done_at'] and not is_waiting(t) for t in items)}" if self.view == "list" else "",
            "log": f"done {len(items)}",
            "settings": "settings",
            "help": "keys",
        }
        title = titles[self.view]
        if len(items) > MAX_ROWS:
            title += f" {i + 1}/{len(items)}"

        footer = (self.msg, "") if self.msg else (FOOTERS[self.view], "dim")
        if self.editing == "new":
            body.append(self.input_row())
        if self.editing is not None:
            footer = ("[p1] task [/d /w /m]  enter  esc", "dim")
        return [self.bar("┌", "┐", title), *body, self.bar("└", "┘", *footer)]

    def render(self):
        lines = self.build()
        if lines == self.last:
            return
        resized = self.last is None or len(lines) != len(self.last)
        self.last = lines
        t = self.text
        t.configure(state="normal", height=len(lines))
        t.delete("1.0", "end")
        for n, line in enumerate(lines):
            if n:
                t.insert("end", "\n")
            for s, tags in line:
                if s == ENTRY:
                    t.window_create("end", window=self.entry)
                else:
                    t.insert("end", s, tags)
        t.configure(state="disabled")
        t.yview_moveto(0)
        if resized:
            self.place()

    def apply(self):
        """Push the current settings onto the window."""
        s = self.s
        bg, fg, dim, sel = THEMES[s["theme"]]
        self.font.configure(size=s["font_size"])
        self.cw = self.font.measure("0")  # one cell; Tk sizes the text widget in these
        self.root.attributes("-topmost", s["on_top"])
        self.root.configure(bg=bg)
        self.text.configure(bg=bg, fg=fg, width=s["width"],  # GAP tabs right-align at the edge
                            tabs=(self.cw * s["width"], "right"), tabstyle="tabular")
        self.text.tag_configure("dim", foreground=dim)
        self.text.tag_configure("struck", foreground=dim, overstrike=True)
        self.text.tag_configure("cursor", background=sel)  # not "sel": Tk reserves that tag
        for n, color in enumerate(PRI):
            self.text.tag_configure(f"p{n}", foreground=color)
        self.render()
        self.place()

    def place(self):
        """Position the window, pulling it back on screen if monitors changed."""
        r = self.root
        r.update_idletasks()
        vx, vy, vw, vh = (ctypes.windll.user32.GetSystemMetrics(n) for n in (76, 77, 78, 79))
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        x, y = self.d["pos"] or (r.winfo_screenwidth() - w - 24, 24)
        r.geometry(f"+{min(max(x, vx), vx + vw - w)}+{min(max(y, vy), vy + vh - h)}")

    def show_in_taskbar(self):
        """Borderless windows get no taskbar entry, so it could be lost behind others."""
        r = self.root
        r.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(r.winfo_id())
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)  # not TOOLWINDOW, but APPWINDOW
        r.withdraw()
        r.after(10, self.reveal)

    def reveal(self):
        self.root.deiconify()
        self.root.focus_force()

    def tick(self):
        if self.editing is None:  # never redraw over text being typed
            self.sweep()
            self.render()
        self.root.after(TICK_MS, self.tick)

    # --- input --------------------------------------------------------------

    def begin_input(self, t=None):
        """Open the input row. Tk destroys the box when render() next clears the text."""
        self.editing = t or "new"
        _, fg, _, sel = THEMES[self.s["theme"]]
        width = self.s["width"] - 4 - sum(len(s) for s, _ in self.input_prefix())
        self.entry = tk.Entry(self.text, font=self.font, bd=0, highlightthickness=0, bg=sel, fg=fg,
                              insertbackground=fg, width=width)
        self.entry.bind("<Return>", self.commit)
        self.entry.bind("<KP_Enter>", self.commit)
        self.entry.bind("<Escape>", self.cancel)
        self.entry.bind("<Tab>", lambda e: "break")
        if t:
            self.entry.insert(0, t["title"])
            self.entry.icursor("end")
        self.render()
        self.entry.focus_force()

    def commit(self, _event=None):
        t, self.editing = self.editing, None
        pri, title, repeat = parse(self.entry.get())
        if title:
            if t == "new":
                t = {"title": title, "pri": pri, "repeat": repeat, "done_at": None,
                     "due": None, "created": time.time()}
                self.d["todos"].append(t)
            else:  # a rename keeps priority and repeat unless new ones were typed
                t["title"] = title
                t["pri"] = t["pri"] if pri is None else pri
                t["repeat"] = repeat or t["repeat"]
            self.save()
            self.follow(t)
        self.root.focus_force()
        self.render()
        return "break"

    def cancel(self, _event=None):
        self.editing = None
        self.root.focus_force()
        self.render()
        return "break"

    def change(self, step):
        key, _, choices = OPTIONS[self.idx]
        now = choices.index(self.s[key]) if self.s[key] in choices else 0
        self.s[key] = choices[(now + step) % len(choices)]
        if key == "autostart" and not self.autostart(self.s[key]):
            self.s[key] = not self.s[key]
        self.save()
        self.apply()

    def list_key(self, k, armed):
        if k == "a":
            return self.begin_input()
        if k == "c":
            return self.sweep(force=True)
        rows = self.rows()
        if not rows:
            return
        t = rows[self.idx]
        if k in ("space", "x"):
            self.toggle(t)
        elif k in ("F2", "e", "Return"):
            return self.begin_input(t)
        elif k.isdigit():
            t["pri"] = None if t["pri"] == int(k) else int(k)
        elif k in ("plus", "equal"):
            t["pri"] = 0 if t["pri"] is None else t["pri"] + 1
        elif k == "minus":
            t["pri"] = None if not t["pri"] else t["pri"] - 1
        elif k == "r":
            t["repeat"] = REPEATS[(REPEATS.index(t["repeat"]) + 1) % len(REPEATS)]
            if not t["repeat"]:
                t["due"] = None
        elif k == "d":
            if armed:
                self.d["todos"].remove(t)
                return self.save()
            self.armed, self.msg = True, "press d again to delete"
            return
        else:
            return
        self.save()
        self.follow(t)

    def on_key(self, e):
        if self.editing is not None or e.state & 0x20004:  # typing, or ctrl/alt held
            return None
        k = e.keysym.lower() if len(e.keysym) == 1 else e.keysym
        armed, self.armed, self.msg = self.armed, False, ""
        if k == "q":
            self.quit()
            return "break"
        if k in ("j", "Down"):
            self.idx += 1
        elif k in ("k", "Up"):
            self.idx -= 1
        elif k == "Tab":
            self.switch("log" if self.view == "list" else "list")
        elif k == "s":
            self.switch("list" if self.view == "settings" else "settings")
        elif k == "question":
            self.switch("list" if self.view == "help" else "help")
        elif k == "Escape":
            self.switch("list")
        elif self.view == "settings":
            if k in ("Left", "h"):
                self.change(-1)
            elif k in ("Right", "l", "space", "Return"):
                self.change(1)
        elif self.view == "list":
            self.list_key(k, armed)
        if self.editing is None:
            self.render()
        return "break"

    def at(self, e):
        line, col = self.text.index(f"@{e.x},{e.y}").split(".")
        return int(line), int(col)

    def row_at(self, line):
        """The item under a line of the window, or None for the borders."""
        row = line - 2 + self.top
        return row if 1 < line < len(self.last) and row < len(self.items()) else None

    def on_press(self, e):
        line, col = self.at(e)
        if line == 1:  # the top border is the drag handle
            self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
            if self.editing is not None:
                self.entry.focus_force()
                return "break"
        elif self.editing is not None:  # clicking away saves, same as enter
            return self.commit()
        self.root.focus_force()
        row = self.row_at(line)
        if row is None:
            return "break"
        again, self.idx = row == self.idx, row
        if self.view == "list":
            t = self.rows()[row]
            if col <= 5:
                self.toggle(t)
                self.save()
                self.follow(t)
            elif again:  # click to highlight, click again to rename
                self.begin_input(t)
                return "break"
        elif self.view == "settings":
            self.change(1)
        self.render()
        return "break"

    def on_double(self, e):
        line, col = self.at(e)
        row = self.row_at(line)
        if self.editing is None and self.view == "list" and col > 5 and row is not None:
            self.begin_input(self.rows()[row])
        return "break"

    def on_motion(self, e):
        if self.drag:
            x, y = e.x_root - self.drag[0], e.y_root - self.drag[1]
            self.d["pos"] = [x, y]
            self.root.geometry(f"+{x}+{y}")
        return "break"

    def on_release(self, _event):
        if self.drag:
            self.drag = None
            self.save()
        return "break"

    def on_wheel(self, e):
        if self.editing is None:
            self.idx += -1 if e.delta > 0 else 1
            self.render()
        return "break"

    def quit(self):
        self.save()
        self.root.destroy()


def main():
    if sys.platform != "win32":
        sys.exit("desktop-todo runs on Windows only.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "desktop-todo-" + str(DATA.resolve()).replace("\\", "/")
    kernel32.CreateMutexW(None, False, name)  # one window per data file; held until exit
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return
    try:
        App().root.mainloop()
    except Exception:  # pythonw has no console, so say why it didn't start
        ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "desktop-todo", 0x10)
        raise


if __name__ == "__main__":
    main()
