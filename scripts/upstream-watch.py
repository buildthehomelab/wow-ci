#!/usr/bin/env python3
"""Watches the server's upstream: the core branch and mod-playerbots (config/core.env).

    upstream-watch.py check     compare the branch heads with the pins; write job outputs
    upstream-watch.py report    open or update the issue once the modules were checked

`check` sets moved=true when a head is ahead of its pin and that pair of heads hasn't
been reported yet. The workflow then syntax-checks every server module against the new
heads, and `report` writes one issue (label `upstream`): what changed upstream, what to
look at before pulling, and which modules stop compiling. The issue is closed again once
the pins match the heads.

Environment: GH_TOKEN, GITHUB_REPOSITORY, and for `report` GITHUB_RUN_ID plus the refs
from `check` (CORE_OLD, CORE_NEW, PLAYERBOTS_OLD, PLAYERBOTS_NEW). BASE_CORE_REF /
BASE_PLAYERBOTS_REF pretend the server is on other commits, DRY_RUN=true only prints,
RECHECK=true reports heads that already have an issue.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LABEL = "upstream"
MAX_COMMITS = 30
MAX_FILES = 300  # GitHub's compare API lists at most this many changed files


def run(*cmd, check=True):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f"{' '.join(cmd)}\n{res.stderr.strip()}")
    return res.stdout


def gh_json(*args):
    return json.loads(run("gh", *args) or "null")


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
    return re.sub(r"\.git$", "", url.split("github.com/", 1)[1])


def head(url, branch):
    out = run("git", "ls-remote", url, f"refs/heads/{branch}")
    if not out.strip():
        sys.exit(f"::error::{url} has no branch {branch}. Was it renamed? Fix config/core.env.")
    return out.split()[0]


def marker(core, playerbots):
    return f"<!-- upstream-watch core={core} playerbots={playerbots} -->"


def open_issue():
    issues = gh_json("issue", "list", "--repo", os.environ["GITHUB_REPOSITORY"], "--label", LABEL,
                     "--state", "open", "--json", "number,body", "--limit", "1")
    return issues[0] if issues else None


def flag(name):
    return os.environ.get(name, "").lower() == "true"


def check():
    env = core_env()
    core_old = os.environ.get("BASE_CORE_REF") or env["CORE_REF"]
    pb_old = os.environ.get("BASE_PLAYERBOTS_REF") or env["PLAYERBOTS_REF"]
    core_new = head(env["CORE_REPO"], env["CORE_BRANCH"])
    pb_new = head(env["PLAYERBOTS_REPO"], env["PLAYERBOTS_BRANCH"])
    print(f"core        {core_old[:7]} -> {core_new[:7]}")
    print(f"playerbots  {pb_old[:7]} -> {pb_new[:7]}")

    issue = None if flag("DRY_RUN") else open_issue()
    moved = (core_old, pb_old) != (core_new, pb_new)
    if not moved:
        print("The pins match upstream.")
        if issue:
            run("gh", "issue", "close", str(issue["number"]), "--repo", os.environ["GITHUB_REPOSITORY"],
                "--comment", "The pins match upstream again.")
    elif issue and marker(core_new, pb_new) in issue["body"] and not flag("RECHECK"):
        print(f"Already reported in #{issue['number']}.")
        moved = False

    pins = dict(line.split("\t", 1) for line in tsv("config/server-modules.tsv"))
    needs = dict(line.split("\t") for line in tsv("config/module-headers.tsv"))
    modules = []
    for folder, pin in pins.items():
        # mod-playerbots is left out: upstream keeps it building against its own core.
        if folder == "mod-playerbots":
            continue
        url, commit = pin.split("\t")
        extra = [pins[n].split("\t") for n in needs.get(folder, "").split()]
        modules.append({"module": folder, "repository": slug(url), "ref": commit,
                        "extra": " ".join(f"{slug(u)}@{c}" for u, c in extra)})

    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"moved={'true' if moved else 'false'}\n")
        out.write(f"core-old={core_old}\ncore-new={core_new}\n")
        out.write(f"playerbots-old={pb_old}\nplayerbots-new={pb_new}\n")
        out.write(f"modules={json.dumps(modules)}\n")


def upstream_section(title, url, old, new, notes):
    """Markdown for one upstream repo, or None when it didn't move."""
    if old == new:
        return None, 0
    repo = slug(url)
    cmp = gh_json("api", f"repos/{repo}/compare/{old}...{new}")
    total = cmp["total_commits"]
    lines = [f"## {title}", "",
             f"`{old[:7]}` → `{new[:7]}` · {total} commit{'s' if total != 1 else ''} · "
             f"[compare](https://github.com/{repo}/compare/{old}...{new})", ""]
    if cmp["status"] != "ahead":
        lines += [f"**The pinned commit is no longer on the branch (status: {cmp['status']}).** "
                  "Upstream rewrote its history, so a plain `git pull` on the server will not work.", ""]

    files = [f["filename"] for f in cmp.get("files", [])]
    found = []
    for text, pattern in notes:
        hits = [f for f in files if re.search(pattern, f)]
        if hits and "{n}" in text:
            found.append(f"- {text.format(n=len(hits))}")
        elif hits:
            found.append(f"- {text}: " + ", ".join(f"`{f}`" for f in hits))
    if found:
        lines += ["Look at these before pulling:", *found]
        if len(files) >= MAX_FILES:
            lines.append(f"- GitHub lists only the first {MAX_FILES} changed files, so this list may be incomplete.")
        lines.append("")

    commits = cmp["commits"][::-1]
    for c in commits[:MAX_COMMITS]:
        subject = c["commit"]["message"].splitlines()[0]
        # No "#123" or "@name": they would link to this repo's issues and ping people.
        subject = re.sub(r"#(?=\d)", "PR ", subject).replace("@", "@ ")
        lines.append(f"- [`{c['sha'][:7]}`]({c['html_url']}) {subject}")
    if total > MAX_COMMITS:
        lines.append(f"- … and {total - MAX_COMMITS} older")
    return "\n".join(lines), total


def module_results():
    """(checked, failed, unchecked): the cpp job of each module in this run."""
    repo, run_id = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    rows = run("gh", "api", "--paginate", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
               "--jq", ".jobs[] | [.name, .conclusion // \"\", .html_url] | @tsv")
    checked, failed, unchecked = [], [], []
    for row in rows.splitlines():
        name, conclusion, url = row.split("\t")
        if " / " not in name:
            continue
        module, job = name.split(" / ", 1)
        if job == "cpp" and conclusion in ("success", "failure"):
            checked.append(module)
            if conclusion == "failure":
                failed.append((module, url))
        elif conclusion not in ("success", "skipped"):
            unchecked.append((module, url))
    return checked, sorted(failed), sorted(set(unchecked) - set(failed))


def report():
    env = core_env()
    core_old, core_new = os.environ["CORE_OLD"], os.environ["CORE_NEW"]
    pb_old, pb_new = os.environ["PLAYERBOTS_OLD"], os.environ["PLAYERBOTS_NEW"]

    core, core_n = upstream_section(
        f"{slug(env['CORE_REPO']).split('/')[1]} ({env['CORE_BRANCH']})", env["CORE_REPO"], core_old, core_new, [
            ("{n} SQL updates", r"^data/sql/updates/"),
            ("Config defaults changed (a renamed key is silently ignored)", r"\.conf\.dist$"),
        ])
    pb, pb_n = upstream_section(
        f"mod-playerbots ({env['PLAYERBOTS_BRANCH']})", env["PLAYERBOTS_REPO"], pb_old, pb_new, [
            ("{n} SQL files", r"^data/sql/"),
            ("Config defaults changed (a renamed key is silently ignored)", r"\.conf\.dist$"),
        ])

    checked, failed, unchecked = module_results()
    mods = ["## Server modules against the new commits", ""]
    if failed:
        mods.append(f"{len(failed)} of {len(checked)} modules with C++ no longer compile. Fix these before pulling:")
        mods += [f"- **{m}** ([log]({url}))" for m, url in failed]
    else:
        mods.append(f"All {len(checked)} modules with C++ still compile.")
    if unchecked:
        mods += ["", "Not checked, because the job itself broke:"]
        mods += [f"- {m} ([log]({url}))" for m, url in unchecked]
    mods += ["", "This is a syntax check of each module at its pinned commit (`config/server-modules.tsv`). "
             "It does not build the core, link, or apply the new SQL."]

    parts = [f"`{env['CORE_BRANCH']}`" + (f" +{core_n}" if core else ""), "mod-playerbots" + (f" +{pb_n}" if pb else "")]
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
        marker(core_new, pb_new),
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
        # Editing doesn't notify anyone; a comment does.
        if marker(core_new, pb_new) not in issue["body"]:
            run("gh", "issue", "comment", number, "--repo", repo, "--body",
                f"Upstream moved again; the report above is updated. {len(failed)} module(s) fail to compile.")
    else:
        run("gh", "label", "create", LABEL, "--repo", repo, "--force", "--color", "1D76DB",
            "--description", "The server's upstream has commits the pins don't")
        run("gh", "issue", "create", "--repo", repo, "--title", title, "--label", LABEL,
            "--body-file", str(body_file))


if __name__ == "__main__":
    {"check": check, "report": report}[sys.argv[1]]()
