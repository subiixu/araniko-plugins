#!/usr/bin/env python3
"""Builds index.json from plugins/<name>.yaml and categories.yaml.

For each entry it downloads https://codeload.github.com/<repo>/tar.gz/<commit>,
reads <path>/plugin.yaml and writes one index.json the app fetches.

    python3 scripts/build_index.py                  # writes ./index.json
    python3 scripts/build_index.py --out /tmp/i.json
    python3 scripts/build_index.py --offline DIR    # DIR/owner__repo__commit/ folders, for tests

Exits 1 with a plain message when an entry is wrong. index.json is left as it
was when only generated_at would change, so its ETag stays the same.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listlib import (  # noqa: E402
    ListError,
    check_entry,
    check_plugin,
    entry_files,
    entry_name,
    fetch_plugin_yaml,
    load_categories,
    load_yaml,
    parse_yaml,
)

INDEX_VERSION = 1


def build(root, offline=None):
    categories = load_categories(root)
    category_ids = {c["id"] for c in categories}
    plugins, seen = [], {}
    for path in entry_files(root):
        name = entry_name(path)
        where = f"plugins/{os.path.basename(path)}"
        if name in seen:
            raise ListError(f"{where}: two entries share the name '{name}' ({seen[name]} and {where})")
        seen[name] = where
        repo, commit, folder = check_entry(load_yaml(path), where)
        text = fetch_plugin_yaml(repo, commit, folder, offline)
        meta = parse_yaml(text, f"{repo}@{commit[:12]}/{folder}/plugin.yaml")
        check_plugin(meta, name, category_ids, where)
        record = dict(meta)
        record.update({"repo": repo, "commit": commit, "path": folder})
        plugins.append(record)
    plugins.sort(key=lambda p: p["name"])
    return {
        "version": INDEX_VERSION,
        "generated_at": int(time.time()),
        "categories": categories,
        "plugins": plugins,
    }


def write_if_changed(index, out):
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as handle:
                old = json.load(handle)
            if {**old, "generated_at": 0} == {**index, "generated_at": 0}:
                return False
        except (OSError, ValueError):
            pass
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, out)
    return True


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Build index.json for the Araniko plugin list.")
    parser.add_argument("--root", default=here, help="the list folder (has plugins/ and categories.yaml)")
    parser.add_argument("--out", help="where to write index.json (default: <root>/index.json)")
    parser.add_argument("--offline", help="read plugins from DIR/owner__repo__commit/ instead of GitHub")
    args = parser.parse_args(argv)
    try:
        index = build(args.root, args.offline)
    except ListError as err:
        print(f"Can't build the list: {err}", file=sys.stderr)
        return 1
    out = args.out or os.path.join(args.root, "index.json")
    changed = write_if_changed(index, out)
    count = len(index["plugins"])
    print(f"{'Wrote' if changed else 'No change to'} {out}: {count} plugin{'s' if count != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
