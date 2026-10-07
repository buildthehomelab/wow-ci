# wow-ci

Shared CI for the buildthehomelab World of Warcraft 3.3.5a repos: AzerothCore modules,
client addons and the portal. Each repo has a ten-line `.github/workflows/ci.yml` that calls
the reusable workflow here. A change to a check lands everywhere at once.

## What runs

Each job runs only if the repo contains that kind of file. **Errors** fail the check.
**Warnings** show up as annotations on the PR diff and in the run's summary.

| Job | Runs when the repo has | Errors (fail the check) | Warnings |
|---|---|---|---|
| **cpp** | `src/*.cpp` | Loader name doesn't match the module folder (`Addmod_fooScripts`). An `Add...Scripts()` is called but never defined. Any compile error against the server's core (Playerbot branch, `config/core.env`) with mod-playerbots headers. | clang `-Wall -Wextra` in module code |
| **sql** | `data/sql/**.sql` | A file fails on a snapshot of the server DB. Applying it twice fails (the server re-applies changed files). A duplicate filename within the module or against the core/other modules. Uninstall SQL where dbimport would run it. | SQL in dirs dbimport never reads; uninstall scripts that fail |
| **addon** | `*.toc` / `*.lua` | `## Interface` isn't 30300. The .toc name doesn't match its folder. A `Blizzard*` folder. Files missing from the .toc or XML includes. Malformed XML. Lua 5.1 syntax errors. Reading a lower-case global that nothing sets. | everything else luacheck finds |
| **php** | `*.php` | `php -l` | |
| **shell** | `*.sh` | shellcheck errors | shellcheck warnings |
| **python** | `*.py` | syntax errors, undefined names (ruff E9/F63/F7/F82) | other pyflakes findings |

### The SQL snapshot

`ghcr.io/buildthehomelab/wow-ci-db` is MySQL 8.4 with the core's base and update SQL, plus
the SQL of every module in `config/server-modules.tsv` at the server's pinned commit. A PR's
SQL is applied on top, the same way `ac-db-import` does it: same directories, filename
order, `mysql --default-character-set=utf8`. That means it runs against the real schema
and real rows (`creature.id`, not `id1`).

`db-image.yml` rebuilds it when `config/` changes, and also every Monday.

## Keeping it in sync with the server

After a pin bump in `wow-server`, refresh the two config files and push. The DB image
rebuilds by itself.

```bash
tail -n +2 ../wow-server/manifest/core.tsv      # -> config/core.env (CORE_REF)
awk -F'\t' 'NR==1{print "folder\turl\tcommit";next}{print $1"\t"$2"\t"$4}' \
  ../wow-server/manifest/modules.tsv >config/server-modules.tsv
```

## Adding CI to a repo

Copy `templates/ci.yml` into the repo's
`.github/workflows/`. Add the repo to `config/repos.txt`. Inputs, all optional:

| Input | Default | Use |
|---|---|---|
| `module` | repo name without `wow-` | the server folder, if it differs (it sets the loader name) |
| `extra-modules` | | `owner/repo@ref` modules whose headers this one includes |
| `lua-exclude` | | globs for vendored Lua outside `Libs/` (e.g. `Astrolabe/**`) |
| `sql-rerun` | `error` | `warning` to only warn on SQL that can't be applied twice |
| `core-ref` | the server's | check against a different core commit |

## Reviews

There's no reviewer bot. For big changes, ask Claude Code for a review (for example
`/code-review` on the PR). [REVIEW.md](REVIEW.md) is the checklist: dupes, bots,
world-thread stalls, era leaks, SQL re-runs, addon taint, object lifetimes.

## Fleet run

**Actions → Fleet run** runs the checks against the default branch of every repo in
`config/repos.txt` (or the ones you list). Use it after changing a check.
