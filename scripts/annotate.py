#!/usr/bin/env python3
"""Turns tool output into GitHub annotations (::error / ::warning) and a job summary.

    annotate.py gcc LOG --root DIR         clang/gcc "file:line:col: warning: msg"
    annotate.py luacheck LOG --root DIR    luacheck --formatter=plain --codes
    annotate.py raw LOG                    lines already in "level|file|line|message" form

GitHub shows only the first 10 annotations of each level per step, so the job summary
lists everything.
"""
import argparse
import os
import re
import sys

GCC = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<level>fatal error|error|warning|note):\s*(?P<msg>.*)$")
LUACHECK = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*\((?P<code>[EW]\d+)\)\s*(?P<msg>.*)$")


def pipe(s):
    return s.replace("|", "\\|")


def esc(s):
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def rel(path, root):
    path = os.path.normpath(path)
    if root:
        root = os.path.abspath(root)
        ap = os.path.abspath(path)
        if ap.startswith(root + os.sep):
            return os.path.relpath(ap, root)
    return path


def emit(items, title):
    seen = set()
    rows = []
    for level, file, line, msg in items:
        key = (level, file, line, msg)
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
        loc = f"file={file},line={line}" if file else ""
        print(f"::{level} {loc}::{esc(msg)}" if loc else f"::{level}::{esc(msg)}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and rows:
        with open(summary, "a") as fh:
            errors = sum(1 for r in rows if r[0] == "error")
            fh.write(f"### {title}: {errors} error(s), {len(rows) - errors} warning(s)\n\n")
            fh.write("| | File | Line | Message |\n|---|---|---|---|\n")
            for level, file, line, msg in rows:
                icon = "❌" if level == "error" else "⚠️"
                fh.write(f"| {icon} | `{file}` | {line} | {pipe(msg)} |\n")
            fh.write("\n")
    return rows


def parse_gcc(text, root):
    items = []
    for raw in text.splitlines():
        m = GCC.match(raw.strip())
        if not m or m["level"] == "note":
            continue
        level = "warning" if m["level"] == "warning" else "error"
        file = rel(m["file"], root)
        # Diagnostics inside core/dependency headers are reported where the module includes them.
        if file.startswith("..") or os.path.isabs(file):
            if level == "warning":
                continue
        items.append((level, file, m["line"], m["msg"]))
    return items


def parse_luacheck(text, root, known=()):
    """Lua findings. Only two kinds fail the job:
      * syntax errors (E*), except invalid escapes like "\\." that Lua 5.1 accepts;
      * reading a lower-case global that nothing in the repo ever sets (113) - a typo or
        a missing `local`/require. Lower-case globals the repo sets in one file and reads
        in another (shared state, SavedVariables) stay warnings.
    """
    rows = []
    set_names = set(known)
    for raw in text.splitlines():
        m = LUACHECK.match(raw.strip())
        if not m:
            continue
        name = re.search(r"'([^']+)'", m["msg"])
        name = name.group(1) if name else ""
        if m["code"] in ("W111", "W112"):
            set_names.add(name)
        rows.append((m, name))
    items = []
    for m, name in rows:
        code = m["code"]
        level = "warning"
        if code.startswith("E") and "invalid escape" not in m["msg"]:
            level = "error"
        elif code == "W113" and re.match(r"^[a-z_]", name) and name not in set_names:
            level = "error"
        items.append((level, rel(m["file"], root), m["line"], f"({code}) {m['msg']}"))
    return items


def parse_raw(text):
    items = []
    for raw in text.splitlines():
        parts = raw.split("|", 3)
        if len(parts) == 4:
            items.append(tuple(parts))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["gcc", "luacheck", "raw"])
    ap.add_argument("log")
    ap.add_argument("--root", default=".")
    ap.add_argument("--title", default=None)
    ap.add_argument("--known", default="", help="comma-separated globals set outside Lua (SavedVariables)")
    a = ap.parse_args()
    with open(a.log, errors="replace") as fh:
        text = fh.read()
    if a.kind == "gcc":
        items = parse_gcc(text, a.root)
    elif a.kind == "luacheck":
        items = parse_luacheck(text, a.root, [k for k in a.known.split(",") if k])
    else:
        items = parse_raw(text)
    rows = emit(items, a.title or {"gcc": "C++", "luacheck": "Lua", "raw": "Checks"}[a.kind])
    sys.exit(1 if any(r[0] == "error" for r in rows) else 0)


if __name__ == "__main__":
    main()
