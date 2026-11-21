"""Shared helpers for the Araniko plugin list: a small YAML reader, entry checks
and plugin downloads. Python 3 standard library only.

The YAML reader covers the subset plugin files use: mappings, block lists
(`- item`, including lists of mappings), flow lists (`[a, b]`), empty `{}`,
plain / single-quoted / double-quoted scalars, `true`/`false`, integers,
`null`/`~`, and `#` comments. Anchors, tags, multi-document files and block
scalars (`|`, `>`) are refused with a plain message.
"""

import io
import json
import os
import posixpath
import re
import tarfile
import urllib.request

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])$")
REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+|$)")
INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")

ENTRY_KEYS = ("repo", "commit", "path")
REQUIRED_PLUGIN_KEYS = ("name", "title", "summary", "author", "version", "icon", "category")
MAX_DOWNLOAD = 100 * 1024 * 1024


class ListError(Exception):
    """A plain-words problem with the list. Shown as is and exits 1."""


# ---------------------------------------------------------------- YAML subset


def _strip_comment(text):
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _lines(source, where):
    out = []
    for number, raw in enumerate(source.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ListError(f"{where} line {number}: use spaces, not tabs, to indent")
        text = _strip_comment(raw)
        if not text.strip():
            continue
        stripped = text.strip()
        if stripped in ("---", "...") and len(out) == 0:
            continue
        if stripped in ("---", "..."):
            raise ListError(f"{where} line {number}: only one document per file")
        indent = len(text) - len(text.lstrip(" "))
        out.append([indent, stripped, number])
    return out


def _split_flow(body, where, number):
    items, quote, depth, current = [], None, 0, ""
    for ch in body:
        if quote:
            current += ch
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += ch
    if quote or depth:
        raise ListError(f"{where} line {number}: unclosed quote or bracket")
    if current.strip():
        items.append(current.strip())
    return items


def _scalar(text, where, number):
    if text.startswith(("|", ">")):
        raise ListError(f"{where} line {number}: multi-line text (| or >) isn't supported; keep it on one line")
    if text.startswith(("&", "*", "!")):
        raise ListError(f"{where} line {number}: anchors and tags aren't supported")
    if text.startswith("["):
        if not text.endswith("]"):
            raise ListError(f"{where} line {number}: a list in brackets must end with ]")
        return [_scalar(item, where, number) for item in _split_flow(text[1:-1], where, number)]
    if text.startswith("{"):
        if text.replace(" ", "") == "{}":
            return {}
        raise ListError(f"{where} line {number}: write mappings on their own lines, not in braces")
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise ListError(f"{where} line {number}: unclosed double quote")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ListError(f"{where} line {number}: can't read this quoted text") from None
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'"):
            raise ListError(f"{where} line {number}: unclosed single quote")
        return text[1:-1].replace("''", "'")
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~", "Null"):
        return None
    if INT_RE.match(text):
        return int(text)
    return text


def _is_item(text):
    return text == "-" or text.startswith("- ")


def _block(lines, i, indent, where):
    if _is_item(lines[i][1]):
        return _list(lines, i, indent, where)
    return _mapping(lines, i, indent, where)


def _list(lines, i, indent, where):
    out = []
    while i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
        _, text, number = lines[i]
        rest = text[1:]
        body = rest.lstrip(" ")
        if not body:
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                value, i = _block(lines, i, lines[i][0], where)
            else:
                value = None
            out.append(value)
            continue
        if KEY_RE.match(body):
            # "- key: value": a mapping whose keys sit at the column after "- ".
            column = indent + 1 + (len(rest) - len(body))
            lines[i] = [column, body, number]
            value, i = _mapping(lines, i, column, where)
            out.append(value)
            continue
        out.append(_scalar(body, where, number))
        i += 1
    if i < len(lines) and lines[i][0] > indent:
        raise ListError(f"{where} line {lines[i][2]}: unexpected indent")
    return out, i


def _mapping(lines, i, indent, where):
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        _, text, number = lines[i]
        if _is_item(text):
            raise ListError(f"{where} line {number}: a list item where a key was expected")
        match = KEY_RE.match(text)
        if not match:
            raise ListError(f"{where} line {number}: expected 'key: value'")
        key = match.group(1)
        if key in out:
            raise ListError(f"{where} line {number}: '{key}' appears twice")
        rest = text[match.end():].strip()
        i += 1
        if rest:
            out[key] = _scalar(rest, where, number)
        elif i < len(lines) and lines[i][0] > indent:
            out[key], i = _block(lines, i, lines[i][0], where)
        elif i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
            out[key], i = _list(lines, i, indent, where)
        else:
            out[key] = None
    if i < len(lines) and lines[i][0] > indent:
        raise ListError(f"{where} line {lines[i][2]}: unexpected indent")
    return out, i


def parse_yaml(source, where="file"):
    lines = _lines(source, where)
    if not lines:
        return None
    value, i = _block(lines, 0, lines[0][0], where)
    if i != len(lines):
        raise ListError(f"{where} line {lines[i][2]}: indent doesn't line up with the lines above")
    return value


def load_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return parse_yaml(handle.read(), os.path.basename(path))


# ---------------------------------------------------------------- the list


def load_categories(root):
    path = os.path.join(root, "categories.yaml")
    data = load_yaml(path)
    if not isinstance(data, list) or not data:
        raise ListError("categories.yaml must be a list of categories")
    seen = set()
    for cat in data:
        if not isinstance(cat, dict) or set(cat) != {"id", "title", "order"}:
            raise ListError("each category in categories.yaml needs exactly id, title and order")
        if cat["id"] in seen:
            raise ListError(f"categories.yaml has the category '{cat['id']}' twice")
        if not isinstance(cat["order"], int):
            raise ListError(f"category '{cat['id']}': order must be a whole number")
        seen.add(cat["id"])
    return sorted(data, key=lambda c: (c["order"], c["id"]))


def entry_files(root):
    folder = os.path.join(root, "plugins")
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    return [os.path.join(folder, n) for n in names if n.endswith((".yaml", ".yml"))]


def entry_name(path):
    base = os.path.basename(path)
    return base.rsplit(".", 1)[0]


def check_entry(entry, where):
    """Checks one plugins/<name>.yaml entry and returns (repo, commit, path)."""
    if not isinstance(entry, dict):
        raise ListError(f"{where}: must have repo, commit and path")
    extra = sorted(set(entry) - set(ENTRY_KEYS))
    if extra:
        raise ListError(f"{where}: unknown key {', '.join(extra)}; only repo, commit and path are allowed")
    missing = [k for k in ENTRY_KEYS if entry.get(k) in (None, "")]
    if missing:
        raise ListError(f"{where}: missing {', '.join(missing)}")
    repo, commit, path = str(entry["repo"]), str(entry["commit"]), str(entry["path"])
    if not REPO_RE.match(repo):
        raise ListError(f"{where}: repo must look like owner/repo, got '{repo}'")
    if not COMMIT_RE.match(commit):
        raise ListError(
            f"{where}: commit must be the full 40-character commit (lowercase 0-9, a-f), got '{commit}'"
        )
    norm = posixpath.normpath(path)
    if path.startswith("/") or norm == ".." or norm.startswith("../") or "\\" in path:
        raise ListError(f"{where}: path must be a folder inside the repo, got '{path}'")
    return repo, commit, norm


def fetch_plugin_yaml(repo, commit, path, offline=None):
    """Returns the text of <path>/plugin.yaml at that commit."""
    if offline:
        folder = os.path.join(offline, f"{repo.replace('/', '__')}__{commit}")
        target = os.path.join(folder, path, "plugin.yaml")
        if not os.path.isfile(target):
            raise ListError(f"{repo} at {commit[:12]}: no plugin.yaml in '{path}'")
        with open(target, encoding="utf-8") as handle:
            return handle.read()
    data = download_tarball(repo, commit)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = find_member(tar, path, "plugin.yaml")
        if member is None:
            raise ListError(f"{repo} at {commit[:12]}: no plugin.yaml in '{path}'")
        return tar.extractfile(member).read().decode("utf-8")


def download_tarball(repo, commit):
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    request = urllib.request.Request(url, headers={"User-Agent": "araniko-plugin-list"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_DOWNLOAD + 1)
    except Exception as err:  # noqa: BLE001 - shown in plain words
        raise ListError(f"couldn't download {repo} at {commit[:12]}: {err}") from None
    if len(data) > MAX_DOWNLOAD:
        raise ListError(f"{repo} at {commit[:12]} is over 100 MB")
    return data


def tar_top(tar):
    for member in tar.getmembers():
        return member.name.split("/", 1)[0]
    return ""


def find_member(tar, path, filename):
    top = tar_top(tar)
    want = posixpath.normpath(posixpath.join(top, path, filename))
    for member in tar.getmembers():
        if member.isfile() and posixpath.normpath(member.name) == want:
            return member
    return None


def check_plugin(meta, name, categories, where):
    if not isinstance(meta, dict):
        raise ListError(f"{where}: plugin.yaml must be a set of keys")
    missing = [k for k in REQUIRED_PLUGIN_KEYS if meta.get(k) in (None, "")]
    if missing:
        raise ListError(f"{where}: plugin.yaml is missing {', '.join(missing)}")
    if meta["name"] != name:
        raise ListError(
            f"{where}: the file is called '{name}' but plugin.yaml says name: {meta['name']}; they must match"
        )
    if not NAME_RE.match(name):
        raise ListError(f"{where}: name must be 2-40 characters of a-z, 0-9 and hyphens")
    if categories is not None and meta["category"] not in categories:
        raise ListError(
            f"{where}: category '{meta['category']}' isn't in categories.yaml ({', '.join(sorted(categories))})"
        )
