#!/usr/bin/env bash
# Compiles every .cpp in a module's src/ with -fsyntax-only against the AzerothCore
# (Playerbot branch) headers. Errors fail the job; warnings become PR annotations.
#
#   CORE_DIR        core checkout (required)
#   PLAYERBOTS_DIR  mod-playerbots checkout (optional; adds -DMOD_PLAYERBOTS + its headers)
#   EXTRA_INCLUDES  extra module checkouts whose src/ dirs are added (space separated)
#   MODULE_DIR      module checkout (default: .)
set -uo pipefail

CORE_DIR=${CORE_DIR:?CORE_DIR not set}
MODULE_DIR=${MODULE_DIR:-.}
CXX=${CXX:-clang++}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${RUNNER_TEMP:-/tmp}/cpp-check
mkdir -p "$OUT"
rsp="$OUT/includes.rsp"
: >"$rsp"

# The module's own headers come first so they win over core headers with the same name.
find "$MODULE_DIR/src" -type d | sed 's/^/-I/' >>"$rsp"
for extra in ${EXTRA_INCLUDES:-}; do
  find "$extra/src" -type d | sed 's/^/-isystem /' >>"$rsp"
done
if [ -n "${PLAYERBOTS_DIR:-}" ]; then
  find "$PLAYERBOTS_DIR/src" -type d | sed 's/^/-isystem /' >>"$rsp"
fi
# Core headers are -isystem so warnings inside them never reach the module's report.
find "$CORE_DIR/src" -type d -not -path "$CORE_DIR/src/tools*" | sed 's/^/-isystem /' >>"$rsp"
for d in fmt/include g3dlite/include utf8cpp SFMT argon2 fkYAML gsoap \
         recastnavigation/Detour/Include recastnavigation/Recast/Include; do
  [ -d "$CORE_DIR/deps/$d" ] && echo "-isystem $CORE_DIR/deps/$d" >>"$rsp"
done
echo "-isystem $CORE_DIR/modules" >>"$rsp"
for d in /usr/include/mysql /usr/local/include /opt/homebrew/include; do
  [ -d "$d" ] && echo "-isystem $d" >>"$rsp"
done

defines=(-DACORE_API_EXPORT_COMMON= -DCONFIG_FILE_LIST= )
[ -n "${PLAYERBOTS_DIR:-}" ] && defines+=(-DMOD_PLAYERBOTS)

# -Wunused-parameter is off: script hooks routinely ignore most of their arguments.
flags=(-std=gnu++20 -fsyntax-only -fno-color-diagnostics -fdiagnostics-absolute-paths
       -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers
       -Wno-deprecated-declarations -Wno-unknown-pragmas)

files=()
while IFS= read -r f; do files+=("$f"); done < <(find "$MODULE_DIR/src" -name '*.cpp' | sort)
if [ ${#files[@]} -eq 0 ]; then
  echo "No .cpp files under $MODULE_DIR/src"
  exit 0
fi

fail=0
log="$OUT/compile.log"
: >"$log"
for f in "${files[@]}"; do
  echo "::group::$f"
  "$CXX" "${flags[@]}" "${defines[@]}" @"$rsp" "$f" >"$OUT/one.log" 2>&1 || fail=1
  cat "$OUT/one.log"
  cat "$OUT/one.log" >>"$log"
  echo "::endgroup::"
done

python3 "$HERE/annotate.py" gcc "$log" --root "$MODULE_DIR"
if [ $fail -ne 0 ]; then
  echo "::error::C++ syntax check failed against core ${CORE_REF:-?}. See the errors above."
  exit 1
fi
echo "C++ syntax check passed (${#files[@]} files)."
