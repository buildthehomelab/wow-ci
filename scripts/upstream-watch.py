#!/usr/bin/env python3
"""Watches the server's upstream: the core branch and mod-playerbots (config/core.env).

    upstream-watch.py check     compare the branch heads with the pins; write job outputs
    upstream-watch.py report    open or update the issue once the modules were checked

`check` sets moved=true when a branch head differs from its pin and this exact state
(heads, pins and module config) has no complete report yet. The workflow then
syntax-checks every server module against the new heads, and `report` writes one issue
(label `upstream`): what changed upstream, what to look at before pulling, and which
modules stop compiling. A report with modules that could not be checked is redone on the
next run. The issue is closed again once the pins match the heads.

Environment: GH_TOKEN, GITHUB_REPOSITORY, and for `report` GITHUB_RUN_ID plus the values
from `check` (CORE_OLD, CORE_NEW, PLAYERBOTS_OLD, PLAYERBOTS_NEW, STATE, MODULES).
BASE_CORE_REF / BASE_PLAYERBOTS_REF pretend the server is on other commits and imply
DRY_RUN=true, which only prints. RECHECK=true reports a state that already has an issue.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LABEL = "upstream"
MAX_COMMITS = 30
# Steps of ci.yml's cpp job that fail because of the module's code. A cpp job that failed
# anywhere else (fetching the core, pulling the image) says nothing about the module.
CODE_STEPS = {"Loader name", "Syntax check against the server's core"}
CONFIG = ("config/server-modules.tsv", "config/module-headers.tsv", "config/module-defines.tsv")


def run(*cmd, check=True):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f"::error::{' '.join(cmd)}: {res.stderr.strip()}")
    return res.stdout


def ok(*cmd):
    return subprocess.run(cmd, capture_output=True).returncode == 0


def core_env():
    env = {}
    for line in (ROOT / "config/core.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def tsv(path):
    """Data rows of a config .tsv: no header, comments or blank lines."""
    rows = [r for r in (ROOT / path).read_text().splitlines() if r.strip() and not r.startswith("#")]
    return rows[1:]


def slug(url):
    return url.split("github.com/", 1)[1].removesuffix(".git")


def head(url, branch):
    out = run("git", "ls-remote", url, f"refs/heads/{branch}")
    if not out.strip():
        sys.exit(f"::error::{url} has no branch {branch}. Was it renamed? Fix config/core.env.")
    return out.split()[0]


def marker(state, complete):
    return f"<!-- upstream-watch state={state} complete={'yes' if complete else 'no'} -->"


def open_issue():
    issues = json.loads(run("gh", "issue", "list", "--repo", os.environ["GITHUB_REPOSITORY"], "--label", LABEL,
                            "--state", "open", "--json", "number,body", "--limit", "1"))
    return issues[0] if issues else None


def flag(name):
    return os.environ.get(name, "").lower() == "true"


def check():
    env = core_env()
    pins = dict(line.split("\t", 1) for line in tsv("config/server-modules.tsv"))
    if "mod-playerbots" in pins and pins["mod-playerbots"].split("\t")[1] != env["PLAYERBOTS_REF"]:
        sys.exit("::error::PLAYERBOTS_REF in config/core.env and the mod-playerbots row of "
                 "config/server-modules.tsv are different commits. Sync both from wow-server.")

    base_core, base_pb = os.environ.get("BASE_CORE_REF"), os.environ.get("BASE_PLAYERBOTS_REF")
    dry = flag("DRY_RUN") or bool(base_core or base_pb)
    core_old, pb_old = base_core or env["CORE_REF"], base_pb or env["PLAYERBOTS_REF"]
    core_new = head(env["CORE_REPO"], env["CORE_BRANCH"])
    pb_new = head(env["PLAYERBOTS_REPO"], env["PLAYERBOTS_BRANCH"])
    print(f"core        {core_old[:7]} -> {core_new[:7]}")
    print(f"playerbots  {pb_old[:7]} -> {pb_new[:7]}")

    # A fixed module or a new pin changes the state, so the report is redone.
    digest = hashlib.sha256("\n".join([core_old, pb_old, core_new, pb_new]).encode())
    for path in CONFIG:
        digest.update((ROOT / path).read_bytes())
    state = digest.hexdigest()[:16]

    issue = None if dry else open_issue()
    moved = (core_old, pb_old) != (core_new, pb_new)
    if not moved:
        print("The pins match upstream.")
        if issue:
            run("gh", "issue", "close", str(issue["number"]), "--repo", os.environ["GITHUB_REPOSITORY"],
                "--comment", "The pins match upstream again.")
    elif issue and marker(state, True) in issue["body"] and not flag("RECHECK"):
        print(f"Already reported in #{issue['number']}.")
        moved = False

    needs = dict(line.split("\t") for line in tsv("config/module-headers.tsv"))
    modules = []
    for folder, pin in pins.items():
        # mod-playerbots is left out: upstream keeps it building against its own core.
        if folder == "mod-playerbots":
            continue
        url, commit = pin.split("\t")
        extra = []
        for need in needs.get(folder, "").split():
            if need not in pins:
                sys.exit(f"::error::config/module-headers.tsv: {folder} needs {need}, "
                         "which is not in config/server-modules.tsv.")
            need_url, need_commit = pins[need].split("\t")
            extra.append(f"{slug(need_url)}@{need_commit}")
        modules.append({"module": folder, "repository": slug(url), "ref": commit, "extra": " ".join(extra)})

    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"moved={'true' if moved else 'false'}\n")
        out.write(f"dry-run={'true' if dry else 'false'}\n")
        out.write(f"state={state}\n")
        out.write(f"core-old={core_old}\ncore-new={core_new}\n")
        out.write(f"playerbots-old={pb_old}\nplayerbots-new={pb_new}\n")
        out.write(f"modules={json.dumps(modules)}\n")


def upstream_section(title, url, branch, old, new, sql_dir):
    """(markdown, commit count) for one upstream repo; (None, 0) when it didn't move."""
    if old == new:
        return None, 0
    repo = slug(url)
    # Commits and trees only: enough for the log and for which paths changed.
    clone = str(pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / f"upstream-{repo.replace('/', '-')}.git")
    if not os.path.exists(clone):
        run("git", "clone", "-q", "--bare", "--filter=blob:none", "--single-branch", "--branch", branch, url, clone)

    def git(*args):
        return run("git", "-C", clone, *args)

    lines = [f"## {title}", ""]
    if not (ok("git", "-C", clone, "cat-file", "-e", f"{old}^{{commit}}")
            or ok("git", "-C", clone, "fetch", "-q", "--filter=blob:none", "origin", old)):
        lines += [f"`{old[:7]}` → `{new[:7]}`", "",
                  f"**The pinned commit `{old[:7]}` no longer exists upstream**, so there is nothing to "
                  "compare with. A plain `git pull` on the server will not work."]
        return "\n".join(lines), 0

    total = int(git("rev-list", "--count", f"{old}..{new}"))
    lines += [f"`{old[:7]}` → `{new[:7]}` · {total} commit{'s' if total != 1 else ''} · "
              f"[compare](https://github.com/{repo}/compare/{old}...{new})", ""]
    if not ok("git", "-C", clone, "merge-base", "--is-ancestor", old, new):
        lines += ["**The pinned commit is not in the branch's history** (upstream rewrote it, or the pin is "
                  "from somewhere else). A plain `git pull` on the server will not work.", ""]

    changed = [row.split("\t") for row in git("diff", "--name-status", "--no-renames", old, new).splitlines()]
    sql = sum(1 for status, path in changed if status == "A" and path.startswith(sql_dir) and path.endswith(".sql"))
    confs = [path for _, path in changed if path.endswith(".conf.dist")]
    found = []
    if sql:
        found.append(f"- {sql} new SQL file{'s' if sql != 1 else ''} under `{sql_dir}`")
    if confs:
        found.append("- Config defaults changed (a renamed key is silently ignored): "
                     + ", ".join(f"`{c}`" for c in confs))
    if found:
        lines += ["Look at these before pulling:", *found, ""]

    # A code block: subjects are other people's text and must not link, mention or render.
    lines.append("```text")
    for row in git("log", f"-n{MAX_COMMITS}", "--format=%h%x09%s", f"{old}..{new}").splitlines():
        sha, _, subject = row.partition("\t")
        lines.append(f"{sha}  {subject.replace('`', chr(39))[:120]}")
    if total > MAX_COMMITS:
        lines.append(f"... and {total - MAX_COMMITS} older")
    lines.append("```")
    return "\n".join(lines), total


def module_results(expected):
    """(compiled, failed, unchecked) from this run's jobs, named "<module> / <job>".

    failed and unchecked are (module, log url) lists. A module is unchecked when its jobs
    are missing, were cancelled, or failed outside the module's own code."""
    repo, run_id = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    out = run("gh", "api", "--paginate", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", "--jq",
              '.jobs[] | {name, conclusion, url: .html_url, '
              'failed: [.steps[]? | select(.conclusion == "failure") | .name]}')
    jobs = {}
    for line in out.splitlines():
        job = json.loads(line)
        module, sep, name = job["name"].partition(" / ")
        if sep:
            jobs.setdefault(module, {})[name] = job

    compiled, failed, unchecked = [], [], []
    for module in expected:
        detect, cpp = jobs.get(module, {}).get("detect"), jobs.get(module, {}).get("cpp")
        if cpp and cpp["conclusion"] == "success":
            compiled.append(module)
        elif cpp and cpp["conclusion"] == "failure" and CODE_STEPS & set(cpp["failed"]):
            failed.append((module, cpp["url"]))
        elif cpp and cpp["conclusion"] == "skipped" and detect and detect["conclusion"] == "success":
            pass  # no C++ in this module
        else:
            unchecked.append((module, (cpp or detect or {}).get("url", "")))
    return compiled, failed, unchecked


def report():
    env = core_env()
    core_old, core_new = os.environ["CORE_OLD"], os.environ["CORE_NEW"]
    pb_old, pb_new = os.environ["PLAYERBOTS_OLD"], os.environ["PLAYERBOTS_NEW"]
    state = os.environ["STATE"]

    core, core_n = upstream_section(f"{slug(env['CORE_REPO']).split('/')[1]} ({env['CORE_BRANCH']})",
                                    env["CORE_REPO"], env["CORE_BRANCH"], core_old, core_new, "data/sql/updates/")
    pb, pb_n = upstream_section(f"mod-playerbots ({env['PLAYERBOTS_BRANCH']})",
                                env["PLAYERBOTS_REPO"], env["PLAYERBOTS_BRANCH"], pb_old, pb_new, "data/sql/")

    compiled, failed, unchecked = module_results([m["module"] for m in json.loads(os.environ["MODULES"])])

    def log(url):
        return f" ([log]({url}))" if url else ""

    mods = ["## Server modules against the new commits", ""]
    if failed:
        mods.append(f"{len(failed)} of {len(compiled) + len(failed)} modules fail the check. "
                    "Fix these before pulling:")
        mods += [f"- **{m}**{log(url)}" for m, url in failed]
    else:
        mods.append(f"All {len(compiled)} checked modules still compile.")
    if unchecked:
        mods += ["", f"{len(unchecked)} could not be checked (the job broke or was cancelled). "
                 "The next run tries again:"]
        mods += [f"- {m}{log(url)}" for m, url in unchecked]
    mods += ["", "This is a syntax check of each module at its pinned commit (`config/server-modules.tsv`). "
             "It does not build the core, link, or apply the new SQL."]

    parts = [f"`{env['CORE_BRANCH']}`" + (f" +{core_n}" if core_n else ""),
             "mod-playerbots" + (f" +{pb_n}" if pb_n else "")]
    title = "Upstream has new commits: " + ", ".join(p for p, s in zip(parts, (core, pb)) if s)
    body = "\n\n".join(filter(None, [
        "The server's upstream moved past the pinned commits (`config/core.env`, copied from "
        "wow-server `manifest/`).",
        "\n".join(mods), core, pb,
        "## Updating\n\n"
        "1. Fix the modules listed above, if any.\n"
        "2. On the server, pull the core and mod-playerbots and rebuild.\n"
        "3. Record the new commits in wow-server `manifest/`, then in `config/core.env` and "
        "`config/server-modules.tsv` here. This issue closes by itself once they match.",
        marker(state, not unchecked),
    ]))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as out:
            out.write(f"# {title}\n\n{body}\n")
    if flag("DRY_RUN"):
        print(f"{title}\n\n{body}")
        return

    repo = os.environ["GITHUB_REPOSITORY"]
    body_file = pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "upstream-issue.md"
    body_file.write_text(body)
    issue = open_issue()
    if issue:
        number = str(issue["number"])
        run("gh", "issue", "edit", number, "--repo", repo, "--title", title, "--body-file", str(body_file))
        # Editing doesn't notify anyone; a comment does. One per new state, not per retry.
        if f"state={state} " not in issue["body"]:
            run("gh", "issue", "comment", number, "--repo", repo, "--body",
                f"The report above is updated: {len(failed)} module(s) fail the check.")
    else:
        run("gh", "label", "create", LABEL, "--repo", repo, "--force", "--color", "1D76DB",
            "--description", "The server's upstream has commits the pins don't")
        run("gh", "issue", "create", "--repo", repo, "--title", title, "--label", LABEL,
            "--body-file", str(body_file))


if __name__ == "__main__":
    {"check": check, "report": report}[sys.argv[1]]()
