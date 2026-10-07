#!/usr/bin/env python3
"""Structure checks for WoW 3.3.5a addons in a repo (run from the repo root).

For each addon .toc (vendored library .tocs under Libs/ are skipped):
  error    ## Interface is not 30300. The client marks the addon out of date and won't
           load it unless "Load out of date AddOns" is ticked.
  error    The folder starts with "Blizzard". The 3.3.5 client renames those to .old at launch.
  error    The .toc name doesn't match its folder. The client ignores the addon.
  warning  The .toc sits at the repo root, so a GitHub ZIP unpacks it into
           <repo>-main/, which doesn't match the .toc name.
  error    A file listed in the .toc doesn't exist.
  warning  A listed file differs only in case. Windows is fine with that; other
           platforms and some launchers aren't.
For every .xml reachable from a .toc:
  error    The XML is malformed.
  error    A <Script file=> or <Include file=> target doesn't exist.
"""
import os
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

INTERFACE = "30300"
findings = []


def add(level, path, line, msg):
    findings.append((level, str(path) if path else "", line or "", msg))


def tracked():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [pathlib.PurePosixPath(p) for p in out.splitlines()]


FILES = tracked()
LOWER = {str(p).lower(): p for p in FILES}
DIRS = {str(d).lower() for p in FILES for d in p.parents}


def resolve(base, ref):
    """Find a tracked file, the client's way: backslashes, case-insensitive."""
    ref = ref.strip().replace("\\", "/")
    target = pathlib.PurePosixPath(os.path.normpath(str(base / ref))) if str(base) != "." else pathlib.PurePosixPath(os.path.normpath(ref))
    exact = target in set(FILES)
    hit = LOWER.get(str(target).lower())
    return target, exact, hit


def is_lib(path):
    return any(part.lower() in ("libs", "lib", "libraries") for part in path.parts[:-1])


xml_seen = set()


def check_xml(path, chain):
    if path in xml_seen:
        return
    xml_seen.add(path)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        add("error", path, e.position[0], f"Malformed XML: {e}")
        return
    except FileNotFoundError:
        return
    base = path.parent
    with open(path, errors="replace") as fh:
        lines = fh.read().splitlines()
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("Script", "Include") or "file" not in el.attrib:
            continue
        ref = el.attrib["file"]
        target, exact, hit = resolve(base, ref)
        line = next((i + 1 for i, l in enumerate(lines) if ref in l), "")
        if not hit:
            add("error", path, line, f'<{tag} file="{ref}"> not found ({target})')
        elif not exact:
            add("warning", path, line, f'<{tag} file="{ref}"> only matches {hit} by case')
        elif tag == "Include" and str(hit).lower().endswith(".xml"):
            check_xml(pathlib.PurePosixPath(hit), chain)


tocs = [p for p in FILES if p.suffix.lower() == ".toc" and not is_lib(p)]
for toc in tocs:
    folder = toc.parent
    text = pathlib.Path(toc).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    iface = next((l.split(":", 1)[1].strip() for l in text if re.match(r"^##\s*Interface\s*:", l, re.I)), None)
    if iface is None:
        add("error", toc, 1, "No `## Interface:` line. The client treats the addon as out of date.")
    elif iface != INTERFACE:
        line = next(i + 1 for i, l in enumerate(text) if re.match(r"^##\s*Interface\s*:", l, re.I))
        add("error", toc, line, f"## Interface is {iface}; the 3.3.5a client needs {INTERFACE} or it marks the addon out of date.")
    if str(folder) == ".":
        add("warning", toc, 1, "This .toc is at the repo root. A GitHub ZIP unpacks it into "
            "<repo>-<branch>/, so the folder won't match the .toc name. Put the addon in its own folder.")
    else:
        if folder.name != toc.stem:
            add("error", toc, 1, f"The .toc is named {toc.name} but sits in folder {folder.name}/. The client only loads <Folder>/<Folder>.toc.")
        if folder.name.lower().startswith("blizzard"):
            add("error", toc, 1, f"Folder {folder.name}/ starts with 'Blizzard'. The 3.3.5 client renames AddOns/Blizzard* folders to .old at launch.")
    for i, raw in enumerate(text):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        target, exact, hit = resolve(folder, entry)
        if not hit:
            if str(target).lower() in DIRS:
                continue
            add("error", toc, i + 1, f"Listed file {entry} does not exist.")
        elif not exact:
            add("warning", toc, i + 1, f"Listed file {entry} only matches {hit} by case.")
        elif str(hit).lower().endswith(".xml"):
            check_xml(pathlib.PurePosixPath(hit), [toc])

for level, file, line, msg in findings:
    loc = f" file={file},line={line}" if file else ""
    print(f"::{level}{loc}::{msg}")
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary and findings:
    with open(summary, "a") as fh:
        fh.write("### Addon structure\n\n| | File | Line | Message |\n|---|---|---|---|\n")
        for level, file, line, msg in findings:
            fh.write(f"| {'❌' if level == 'error' else '⚠️'} | `{file}` | {line} | {msg} |\n")
        fh.write("\n")
errors = sum(1 for f in findings if f[0] == "error")
print(f"Addon structure: {len(tocs)} addon(s), {len(xml_seen)} XML file(s), {errors} error(s), {len(findings) - errors} warning(s).")
sys.exit(1 if errors else 0)
