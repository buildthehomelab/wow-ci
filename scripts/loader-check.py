#!/usr/bin/env python3
"""Link-time checks the syntax-only build can't see.

1. AzerothCore calls Add<folder>Scripts() for each module, with '-' turned into '_'
   (modules/CMakeLists.txt). The folder is the module name (mod-foo), not the repo name
   (wow-mod-foo), so a module needs `void Addmod_fooScripts()`.
   A module whose own CMakeLists.txt calls AC_ADD_SCRIPT_LOADER("Foo" "<header>") uses the
   core's deprecated loader API: the core calls AddFooScripts(), and skips the
   folder-derived name when the header's path contains the module folder.
2. Every Add...Scripts() the module declares and calls must also be defined somewhere in
   src/. A missing one only shows up as an undefined reference at link time.

    loader-check.py MODULE_NAME [SRC_DIR]
"""
import pathlib
import re
import sys

name = sys.argv[1]
src = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "src")
expected = "Add" + name.replace("-", "_") + "Scripts"
source = f"the module folder `{name}`"
cmake = src.parent / "CMakeLists.txt"
if cmake.is_file():
    old_api = re.search(r'^\s*AC_ADD_SCRIPT_LOADER\(\s*"(\w+)"\s+"([^"]*)"', cmake.read_text(errors="replace"), re.M)
    # Only a header inside the module folder makes the core skip the folder-derived loader.
    if old_api and ("CMAKE_CURRENT_LIST_DIR" in old_api.group(2) or name in old_api.group(2)):
        expected = f"Add{old_api.group(1)}Scripts"
        source = "AC_ADD_SCRIPT_LOADER in its CMakeLists.txt"

files = sorted(p for p in src.rglob("*") if p.suffix in (".cpp", ".h", ".hpp"))
code = {}
for p in files:
    text = p.read_text(errors="replace")
    # Strip comments so commented-out declarations don't count.
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    code[p] = text

DEF = re.compile(r"\bvoid\s+(Add\w*Scripts)\s*\(\s*(?:void)?\s*\)\s*\{")
DECL = re.compile(r"\bvoid\s+(Add\w*Scripts)\s*\(\s*(?:void)?\s*\)\s*;")
CALL = re.compile(r"(?<![\w:])(Add\w*Scripts)\s*\(\s*\)\s*;")

defined, declared, called = {}, {}, {}
for p, text in code.items():
    for rx, table in ((DEF, defined), (DECL, declared)):
        for m in rx.finditer(text):
            table.setdefault(m.group(1), (p, text.count("\n", 0, m.start()) + 1))
    for m in CALL.finditer(text):
        before = text[max(0, m.start() - 6):m.start()]
        if "void" in before:
            continue
        called.setdefault(m.group(1), (p, text.count("\n", 0, m.start()) + 1))

errors = []
if expected not in defined:
    hint = ""
    others = [n for n in defined if n.startswith("Addmod_") or n.startswith("Addmod")]
    if others:
        hint = f" Found {', '.join(others)} instead; is the module folder name right?"
    errors.append(("", "", f"Missing loader `void {expected}()`. AzerothCore derives it from {source}, so the core will fail to link.{hint}"))

for fn, (p, line) in called.items():
    if fn not in defined and fn != expected:
        errors.append((str(p), line, f"`{fn}()` is called but never defined in src/ (undefined reference at link time)."))

for file, line, msg in errors:
    loc = f" file={file},line={line}" if file else ""
    print(f"::error{loc}::{msg}")

if errors:
    sys.exit(1)
print(f"Loader OK: {expected}() defined; {len(called)} script registration call(s) resolved.")
