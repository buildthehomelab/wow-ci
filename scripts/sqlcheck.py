#!/usr/bin/env python3
"""Apply AzerothCore module SQL the way ac-db-import does, and report problems.

The rules are taken from src/server/database/Updater (UpdateFetcher.cpp, DBUpdater.cpp):
  * Every directory directly under <module>/data/sql/ whose name contains "world",
    "characters" or "auth" is searched recursively (10 levels deep) for *.sql.
  * One filename namespace per database, covering the core updates and every module. Two
    files with the same name stop dbimport ("Duplicate filename ... occurred").
  * Files are applied in filename order with `mysql --default-character-set=utf8`. The
    first failing statement aborts the import, which keeps worldserver down.
  * When a file's hash changes, the whole file runs again on the live DB, so module SQL
    has to be safe to apply twice.

Commands:
  sqlcheck.py files MODULE_DIR                          list (db, path) the server would apply
  sqlcheck.py apply MODULE_DIR --module NAME [--record]  apply them; used to build the image
  sqlcheck.py check MODULE_DIR --module NAME            the CI check (dupes, apply, re-apply, uninstall)

Connection settings come from MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PWD.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

DBS = {"world": "acore_world", "characters": "acore_characters", "auth": "acore_auth"}
MAX_DEPTH = 10


def pipe(s):
    return s.replace("|", "\\|")


def mysql_args(database=None):
    args = ["mysql", "-h", os.environ.get("MYSQL_HOST", "127.0.0.1"), "-P", os.environ.get("MYSQL_PORT", "3306"),
            "-u", os.environ.get("MYSQL_USER", "root"), "--default-character-set=utf8", "--max-allowed-packet=1GB"]
    if database:
        args.append(database)
    return args


def run_sql_file(path, database):
    with open(path, "rb") as fh:
        p = subprocess.run(mysql_args(database), stdin=fh, capture_output=True)
    return p.returncode, p.stderr.decode(errors="replace").strip()


def query(sql, database=None):
    p = subprocess.run(mysql_args(database) + ["-N", "-B", "-e", sql], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return [line.split("\t") for line in p.stdout.splitlines()]


def walk(path, depth, out):
    for entry in sorted(path.iterdir()):
        if entry.is_dir():
            if depth < MAX_DEPTH:
                walk(entry, depth + 1, out)
        elif entry.suffix == ".sql":
            out.append(entry)


def module_files(module_dir):
    """[(db_key, path)] in the order dbimport applies them (per DB, by filename)."""
    root = pathlib.Path(module_dir) / "data" / "sql"
    result = []
    if not root.is_dir():
        return result
    for key in DBS:
        files = []
        for d in sorted(root.iterdir()):
            if d.is_dir() and key in d.name:
                walk(d, 1, files)
        files.sort(key=lambda p: p.name)
        result += [(key, f) for f in files]
    return result


def uninstall_files(module_dir):
    """Uninstall scripts aren't auto-applied; match them to a DB by name."""
    out = []
    for f in sorted(pathlib.Path(module_dir).rglob("*.sql")):
        if ".git" in f.parts or "uninstall" not in str(f).lower():
            continue
        if any(f == p for _, p in module_files(module_dir)):
            continue
        key = next((k for k in ("characters", "world", "auth") if k in f.name.lower() or k in f.parent.name.lower()), None)
        if key:
            out.append((key, f))
    return out


ERR = re.compile(r"ERROR (\d+) \(([^)]+)\) at line (\d+)(?: in file: '[^']*')?: (.*)", re.S)


def parse_error(stderr):
    m = ERR.search(stderr)
    if m:
        return int(m.group(3)), f"MySQL error {m.group(1)}: {m.group(4).strip()}"
    return 1, stderr.splitlines()[-1] if stderr else "mysql failed"


class Report:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.rows = []

    def add(self, level, path, line, msg):
        rel = ""
        if path:
            p = pathlib.Path(path).resolve()
            rel = str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(path)
        self.rows.append((level, rel, line or "", msg))
        loc = f" file={rel},line={line or 1}" if rel else ""
        print(f"::{level}{loc}::{msg}".replace("\n", "%0A"), flush=True)

    @property
    def errors(self):
        return sum(1 for r in self.rows if r[0] == "error")

    def summary(self, title):
        out = os.environ.get("GITHUB_STEP_SUMMARY")
        if not out or not self.rows:
            return
        with open(out, "a") as fh:
            fh.write(f"### {title}\n\n| | File | Line | Message |\n|---|---|---|---|\n")
            for level, f, line, msg in self.rows:
                icon = {"error": "❌", "warning": "⚠️"}.get(level, "ℹ️")
                fh.write(f"| {icon} | `{f}` | {line} | {pipe(msg)} |\n")
            fh.write("\n")


def cmd_files(a):
    for key, f in module_files(a.module_dir):
        print(f"{DBS[key]}\t{f}")


def cmd_apply(a):
    """Image build: apply a module's SQL; never abort the build, just report."""
    failed = 0
    for key, f in module_files(a.module_dir):
        rc, err = run_sql_file(f, DBS[key])
        if rc:
            failed += 1
            line, msg = parse_error(err)
            print(f"::warning::{a.module}: {f.name} line {line}: {msg}")
        if a.record:
            query("INSERT IGNORE INTO ci_meta.sql_files (db, module, name, ok) VALUES "
                  f"('{DBS[key]}', '{a.module}', '{f.name}', {0 if rc else 1})")
    print(f"{a.module}: {len(module_files(a.module_dir))} file(s), {failed} failed")


def cmd_check(a):
    rep = Report(a.module_dir)
    files = module_files(a.module_dir)
    root = pathlib.Path(a.module_dir) / "data" / "sql"

    # SQL the server never picks up: probably meant to be applied, or a misnamed dir.
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and not any(k in d.name for k in DBS) and d.name.lower() not in ("uninstall", "backup", "tools"):
                for f in sorted(d.rglob("*.sql")):
                    rep.add("warning", f, 1, f"data/sql/{d.name}/ doesn't contain 'world', 'characters' or 'auth', "
                            "so ac-db-import never applies this file.")
        for f in sorted(root.glob("*.sql")):
            rep.add("warning", f, 1, "SQL directly in data/sql/ is never applied. Put it in data/sql/db-world/ (or db-characters/, db-auth/).")

    for key, f in files:
        low = str(f.relative_to(root)).lower()
        if "uninstall" in low or "backup" in low or "rollback" in low:
            rep.add("error", f, 1, "This looks like an uninstall/backup script, but it sits where ac-db-import applies it on "
                    "every server start. Move it to data/sql/uninstall/.")

    # Duplicate filenames: within the module, and against the core and the other server modules.
    seen = {}
    for key, f in files:
        if (key, f.name) in seen:
            rep.add("error", f, 1, f"Duplicate filename: {seen[(key, f.name)]} has the same name. ac-db-import aborts "
                    "on duplicate names within one database.")
        seen[(key, f.name)] = str(f.relative_to(a.module_dir))
    try:
        others = query(f"SELECT db, module, name FROM ci_meta.sql_files WHERE module <> '{a.module}'")
    except RuntimeError:
        others = []
    taken = {(db, name): mod for db, mod, name in others}
    for key, f in files:
        mod = taken.get((DBS[key], f.name))
        if mod:
            rep.add("error", f, 1, f"{DBS[key]} already has an update file named {f.name} (from {mod}). ac-db-import "
                    "aborts on duplicate names, so give this file a module-specific name.")

    if not files:
        print("No SQL that ac-db-import would apply.")
    applied = []
    for key, f in files:
        rc, err = run_sql_file(f, DBS[key])
        if rc:
            line, msg = parse_error(err)
            rep.add("error", f, line, f"{msg}. ac-db-import would stop here and worldserver would not start.")
        else:
            applied.append((key, f))
    print(f"Applied {len(applied)}/{len(files)} file(s) on top of the server DB snapshot.")

    # The live server re-runs a file whenever it changes.
    for key, f in applied:
        rc, err = run_sql_file(f, DBS[key])
        if rc:
            line, msg = parse_error(err)
            rep.add(a.rerun_level, f, line, f"Fails when applied a second time ({msg}). The server re-runs a file "
                    "whenever it changes, so an edit to this file would stop ac-db-import. Use DELETE-before-INSERT, "
                    "REPLACE, CREATE TABLE IF NOT EXISTS, or a new update file instead.")

    for key, f in uninstall_files(a.module_dir):
        rc, err = run_sql_file(f, DBS[key])
        if rc:
            line, msg = parse_error(err)
            rep.add("warning", f, line, f"Uninstall script fails after a fresh install ({msg}).")
        else:
            print(f"Uninstall OK: {f}")

    rep.summary("SQL")
    if rep.errors:
        print(f"::error::SQL check failed with {rep.errors} error(s).")
        sys.exit(1)
    print("SQL check passed.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("files"); p.add_argument("module_dir"); p.set_defaults(fn=cmd_files)
    p = sub.add_parser("apply"); p.add_argument("module_dir"); p.add_argument("--module", required=True)
    p.add_argument("--record", action="store_true"); p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("check"); p.add_argument("module_dir"); p.add_argument("--module", required=True)
    p.add_argument("--rerun-level", default="error", choices=["error", "warning"]); p.set_defaults(fn=cmd_check)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
