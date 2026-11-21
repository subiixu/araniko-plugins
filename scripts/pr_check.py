#!/usr/bin/env python3
"""The pull request check for the Araniko plugin list.

For each plugins/<name>.yaml a pull request adds, changes or removes, it
downloads the plugin at the pinned commit, runs `araniko plugin check` on it,
lists what the plugin can do (`screens:` places and `can:`) and flags any change
to `can:` or `reach` against the version listed on main. It writes one Markdown
comment and exits 1 if any entry fails.

    python3 scripts/pr_check.py --base origin/main --head pr-head \
        --araniko /path/to/araniko --comment comment.md

Entry files are read from git as data (`git show`), never run. Run this from
the list folder with the base branch checked out.
"""

import argparse
import io
import os
import posixpath
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listlib import (  # noqa: E402
    ListError,
    check_entry,
    check_plugin,
    download_tarball,
    entry_name,
    load_categories,
    parse_yaml,
    tar_top,
)

MARKER = "<!-- araniko-plugin-check -->"
CAN_LABELS = {
    "send_briefs": "Send briefs to the project's Nayo",
    "storage": "Keep its own storage on the Mac",
    "inbox": "Inbox",
}


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def git_show(ref, path):
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def changed_entries(base, head):
    out = git("diff", "--name-only", f"{base}...{head}", "--", "plugins/")
    return [p for p in out.split() if p.endswith((".yaml", ".yml"))]


def extract_plugin(repo, commit, path, dest):
    """Downloads the tarball and extracts only <path>/ into dest. Returns plugin.yaml text."""
    data = download_tarball(repo, commit)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        top = tar_top(tar)
        prefix = posixpath.normpath(posixpath.join(top, path))
        members = []
        for member in tar.getmembers():
            name = posixpath.normpath(member.name)
            if not (name == prefix or name.startswith(prefix + "/")):
                continue
            if not (member.isfile() or member.isdir()):
                continue  # no links, devices or fifos
            rel = posixpath.relpath(name, prefix)
            if rel.startswith("..") or rel.startswith("/"):
                continue
            member.name = rel
            members.append(member)
        tar.extractall(dest, members=members, filter="data")
    target = os.path.join(dest, "plugin.yaml")
    if not os.path.isfile(target):
        raise ListError(f"{repo} at {commit[:12]}: no plugin.yaml in '{path}'")
    with open(target, encoding="utf-8") as handle:
        return handle.read()


def fetch_meta(repo, commit, path):
    with tempfile.TemporaryDirectory() as tmp:
        text = extract_plugin(repo, commit, path, tmp)
    return parse_yaml(text, "plugin.yaml")


def can_lines(meta):
    lines = []
    for screen in meta.get("screens") or []:
        if isinstance(screen, dict):
            lines.append(f"Shows up in `{screen.get('place')}`: {screen.get('title') or screen.get('id')}")
    can = meta.get("can") or {}
    if not isinstance(can, dict):
        return lines + ["`can:` isn't a set of keys"]
    for key, value in can.items():
        if key == "reach":
            hosts = value if isinstance(value, list) else [value]
            word = "website" if len(hosts) == 1 else "websites"
            lines.append(f"Reach {len(hosts)} {word}: " + ", ".join(f"`{h}`" for h in hosts))
        elif key == "inbox":
            lines.append(f"Inbox: `{value}`")
        elif value is True:
            lines.append(CAN_LABELS.get(key, f"`{key}`"))
        elif value not in (False, None):
            lines.append(f"`{key}`: `{value}`")
    return lines


def can_changes(old, new):
    """Plain-words list of what changed in `can:` (and its reach hosts) from old to new."""
    old_can = (old or {}).get("can") or {}
    new_can = (new or {}).get("can") or {}
    if not isinstance(old_can, dict) or not isinstance(new_can, dict):
        return ["`can:` changed shape"] if old_can != new_can else []
    out = []
    for key in sorted(set(old_can) | set(new_can)):
        before, after = old_can.get(key), new_can.get(key)
        if before == after:
            continue
        if key == "reach":
            b = set(before or []) if isinstance(before, list) else {before} - {None}
            a = set(after or []) if isinstance(after, list) else {after} - {None}
            for host in sorted(a - b):
                out.append(f"**New website in `reach`:** `{host}`")
            for host in sorted(b - a):
                out.append(f"Website no longer in `reach`: `{host}`")
        elif before in (None, False):
            out.append(f"**Now asks for `{key}`:** `{after}`")
        elif after in (None, False):
            out.append(f"No longer asks for `{key}`")
        else:
            out.append(f"**`{key}` changed:** `{before}` → `{after}`")
    return out


def run_check(araniko, repo, commit, path):
    if not araniko:
        return None, "The `araniko` command wasn't available, so `araniko plugin check` didn't run."
    with tempfile.TemporaryDirectory() as tmp:
        extract_plugin(repo, commit, path, tmp)
        result = subprocess.run(
            [araniko, "plugin", "check", tmp], capture_output=True, text=True, timeout=300
        )
    output = (result.stdout + result.stderr).strip().replace(tmp, "<plugin>")
    return result.returncode == 0, output


def review_entry(file, base, head, araniko, category_ids):
    name = entry_name(file)
    lines = [f"### `{name}`"]
    head_text = git_show(head, file)
    base_text = git_show(base, file)
    if head_text is None:
        lines.append("Removes this plugin from the list.")
        return True, lines
    ok = True
    try:
        repo, commit, path = check_entry(parse_yaml(head_text, file), file)
        meta = fetch_meta(repo, commit, path)
        check_plugin(meta, name, category_ids, file)
    except ListError as err:
        lines.append(f"**Can't list this:** {err}")
        return False, lines
    link = f"https://github.com/{repo}/tree/{commit}/{'' if path == '.' else path}".rstrip("/")
    lines.append(f"{meta.get('title')} {meta.get('version')} from [{repo}@{commit[:12]}]({link})")
    lines.append("")

    passed, output = run_check(araniko, repo, commit, path)
    if passed is None:
        lines.append(f"⚠️ {output}")
        ok = False
    elif passed:
        lines.append("`araniko plugin check`: passed")
    else:
        lines.append("`araniko plugin check`: **failed**")
        ok = False
    if output and passed is not None:
        lines += ["", "<details><summary>Check output</summary>", "", "```", output, "```", "</details>"]

    lines += ["", "**What it can do**"]
    lines += [f"- {line}" for line in can_lines(meta)] or ["- Nothing beyond its instructions (no `screens:` or `can:`)"]

    if base_text is None:
        lines += ["", "New to the list."]
    else:
        try:
            old_repo, old_commit, old_path = check_entry(parse_yaml(base_text, file), file)
            old_meta = fetch_meta(old_repo, old_commit, old_path)
            changes = can_changes(old_meta, meta)
            lines += ["", f"**Since the listed version** ({old_meta.get('version')}, {old_commit[:12]})"]
            lines += [f"- {c}" for c in changes] or ["- No change to `can:` or `reach`"]
        except ListError as err:
            lines += ["", f"Couldn't read the listed version to compare: {err}"]
    return ok, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a pull request to the Araniko plugin list.")
    parser.add_argument("--base", required=True, help="git ref of main")
    parser.add_argument("--head", required=True, help="git ref of the pull request")
    parser.add_argument("--araniko", help="path to the araniko command")
    parser.add_argument("--comment", required=True, help="where to write the Markdown comment")
    args = parser.parse_args(argv)

    files = changed_entries(args.base, args.head)
    body = [MARKER, "## Plugin list check", ""]
    ok = True
    if not files:
        body.append("This pull request doesn't change anything in `plugins/`.")
    if len(files) > 1:
        body += [f"⚠️ This pull request changes {len(files)} entries. Please send one pull request per plugin.", ""]
    try:
        category_ids = {c["id"] for c in load_categories(".")}
    except ListError as err:
        category_ids = None
        body += [f"**categories.yaml:** {err}", ""]
        ok = False
    for file in files:
        entry_ok, lines = review_entry(file, args.base, args.head, args.araniko, category_ids)
        ok = ok and entry_ok
        body += lines + [""]
    body.append("Subaz and Claude review every pull request; merged means listed.")
    with open(args.comment, "w", encoding="utf-8") as handle:
        handle.write("\n".join(body) + "\n")
    print("\n".join(body))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
