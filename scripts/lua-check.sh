#!/usr/bin/env bash
# Lua checks for addon code.
#   1. luac5.1 -p on every tracked .lua file: the 3.3.5 client runs Lua 5.1, so this is
#      the real syntax check (blocking).
#   2. luacheck with the WoW config. Only reads of lower-case globals that nothing sets are
#      blocking; everything else is an annotation.
#
#   LUA_EXCLUDE  extra space-separated globs to skip (vendored libraries outside Libs/)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${RUNNER_TEMP:-/tmp}/lua-check
rm -rf "$OUT" && mkdir -p "$OUT/src"
LUAC=${LUAC:-luac5.1}

# Only addon code: Lua under a folder that has a .toc. Other Lua in the repo (dev tools,
# test harnesses run with a modern Lua) never reaches the 5.1 client.
files=()
while IFS= read -r f; do files+=("$f"); done < <(python3 - <<'PY'
import subprocess, posixpath
tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.splitlines()
roots = {posixpath.dirname(f) for f in tracked if f.lower().endswith(".toc")}
for f in tracked:
    if f.endswith(".lua"):
        d = posixpath.dirname(f)
        while True:
            if d in roots:
                print(f)
                break
            if not d:
                break
            d = posixpath.dirname(d)
PY
)
[ ${#files[@]} -eq 0 ] && { echo "No addon Lua files (Lua under a folder with a .toc)."; exit 0; }

fail=0
if command -v "$LUAC" >/dev/null; then
  : >"$OUT/luac.log"
  for f in "${files[@]}"; do
    "$LUAC" -p -o /dev/null "$f" 2>>"$OUT/luac.log" || fail=1
  done
  # luac5.1: "luac5.1: file.lua:12: unexpected symbol near 'x'"
  sed -E 's/^[^:]+: ([^:]+):([0-9]+): (.*)$/error|\1|\2|Lua 5.1 syntax error: \3/' "$OUT/luac.log" >"$OUT/luac.raw"
  python3 "$HERE/annotate.py" raw "$OUT/luac.raw" --title "Lua 5.1 syntax" || fail=1
else
  echo "::warning::$LUAC not found; skipping the Lua 5.1 syntax check"
fi

# luacheck parses with 5.2+ rules and gives up on a whole file at the first escape that
# 5.1 accepts ("\U" is just "U" there). Lint a copy with those escapes neutralised; the
# replacement keeps every line and column where it was.
python3 - "$OUT/src" "${files[@]}" <<'EOF'
import os, re, sys
dst, files = sys.argv[1], sys.argv[2:]
valid = set('abfnrtv\\"\'\n\r0123456789xz[]')
for f in files:
    text = open(f, encoding="latin-1").read()
    text = re.sub(r"\\(.)", lambda m: m.group(0) if m.group(1) in valid else "\\\\", text, flags=re.S)
    path = os.path.join(dst, f)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="latin-1").write(text)
EOF

# SavedVariables are created by the client, not by any Lua line.
known=$(git ls-files '*.toc' | xargs -r grep -hiE '^## *SavedVariables(PerCharacter)?:' 2>/dev/null \
  | sed -E 's/^[^:]*://' | tr ',' '\n' | tr -d ' \r' | grep -v '^$' | paste -sd, -)

excludes=()
for g in ${LUA_EXCLUDE:-}; do excludes+=(--exclude-files "$g"); done
cfg="$HERE/../config/luacheckrc.lua"
(cd "$OUT/src" && luacheck --config "$cfg" --formatter plain --codes --no-color \
   ${excludes[@]+"${excludes[@]}"} "${files[@]}" >"$OUT/luacheck.log" 2>&1)
python3 "$HERE/annotate.py" luacheck "$OUT/luacheck.log" --known "$known" --title "luacheck" || fail=1

if [ $fail -ne 0 ]; then
  echo "::error::Lua check failed. See the errors above."
  exit 1
fi
echo "Lua check passed (${#files[@]} files)."
