import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
REPOS = os.path.join(FIXTURES, "repos")
sys.path.insert(0, SCRIPTS)

import listlib  # noqa: E402
from pr_check import can_changes, can_lines  # noqa: E402


def run_build(list_name, out):
    return subprocess.run(
        [
            sys.executable,
            os.path.join(SCRIPTS, "build_index.py"),
            "--root",
            os.path.join(FIXTURES, "lists", list_name),
            "--offline",
            REPOS,
            "--out",
            out,
        ],
        capture_output=True,
        text=True,
    )


class BuildIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "index.json")

    def tearDown(self):
        self.tmp.cleanup()

    def refused(self, list_name, words):
        result = run_build(list_name, self.out)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(words, result.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_builds_sorted_index(self):
        result = run_build("ok", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.out) as handle:
            index = json.load(handle)
        self.assertEqual(index["version"], 1)
        self.assertIsInstance(index["generated_at"], int)
        self.assertEqual([c["id"] for c in index["categories"]], ["shipping", "docs"])
        self.assertEqual([p["name"] for p in index["plugins"]], ["good-one", "second"])
        good, second = index["plugins"]
        self.assertEqual(good["summary"], "A test plugin: it does nothing")
        self.assertEqual(good["commit"], "1" * 40)
        self.assertEqual(good["repo"], "example/good")
        self.assertEqual(good["path"], ".")
        self.assertEqual(good["settings"][0]["options"], ["Plain", "Friendly"])
        self.assertEqual(second["path"], "second")
        self.assertEqual(second["can"], {"storage": True, "reach": ["api.example.com"]})
        self.assertEqual(second["screens"][0]["place"], "project-tab")

    def test_unchanged_index_is_not_rewritten(self):
        self.assertEqual(run_build("ok", self.out).returncode, 0)
        with open(self.out) as handle:
            first = handle.read()
        again = run_build("ok", self.out)
        self.assertIn("No change", again.stdout)
        with open(self.out) as handle:
            self.assertEqual(handle.read(), first)

    def test_refuses_name_mismatch(self):
        self.refused("name-mismatch", "the file is called 'wrong-name' but plugin.yaml says name: something-else")

    def test_refuses_short_commit(self):
        self.refused("short-commit", "full 40-character commit")

    def test_refuses_duplicate_names(self):
        self.refused("duplicate", "two entries share the name 'good-one'")

    def test_refuses_unknown_category(self):
        self.refused("unknown-category", "category 'shipping' isn't in categories.yaml")


class EntryTest(unittest.TestCase):
    def test_commit_must_be_lowercase_hex(self):
        for commit in ("A" * 40, "g" * 40, "1" * 39, "1" * 41):
            with self.assertRaises(listlib.ListError):
                listlib.check_entry({"repo": "a/b", "commit": commit, "path": "."}, "x")

    def test_path_must_stay_inside_repo(self):
        for path in ("/etc", "..", "../x", "a/../../b"):
            with self.assertRaises(listlib.ListError):
                listlib.check_entry({"repo": "a/b", "commit": "a" * 40, "path": path}, "x")
        self.assertEqual(listlib.check_entry({"repo": "a/b", "commit": "a" * 40, "path": "x/"}, "x")[2], "x")

    def test_unknown_keys_refused(self):
        with self.assertRaises(listlib.ListError):
            listlib.check_entry({"repo": "a/b", "commit": "a" * 40, "path": ".", "url": "x"}, "x")


class YamlTest(unittest.TestCase):
    def test_subset(self):
        data = listlib.parse_yaml(
            "a: 1  # comment\n"
            "b: 'it''s'\n"
            'c: "x # not a comment"\n'
            "d: [x, 'y, z']\n"
            "e:\n- one\n- k: v\n  w: true\n"
            "f: {}\n"
            "g: 0.1.0\n"
            "h:\n"
        )
        self.assertEqual(
            data,
            {
                "a": 1,
                "b": "it's",
                "c": "x # not a comment",
                "d": ["x", "y, z"],
                "e": ["one", {"k": "v", "w": True}],
                "f": {},
                "g": "0.1.0",
                "h": None,
            },
        )

    def test_refusals(self):
        for text in ("a: |\n  x\n", "a: 1\na: 2\n", "a:\n\t- x\n", "a: [x\n", "a: 1\n   b: 2\n"):
            with self.assertRaises(listlib.ListError, msg=text):
                listlib.parse_yaml(text)


class PrCheckTest(unittest.TestCase):
    def test_can_changes_flags_new_reach_host(self):
        old = {"can": {"storage": True, "reach": ["api.github.com"]}}
        new = {"can": {"storage": True, "inbox": "read", "reach": ["api.github.com", "evil.example"]}}
        changes = can_changes(old, new)
        self.assertIn("**New website in `reach`:** `evil.example`", changes)
        self.assertIn("**Now asks for `inbox`:** `read`", changes)
        self.assertEqual(can_changes(old, old), [])

    def test_can_lines(self):
        lines = can_lines(
            {
                "screens": [{"id": "f", "place": "project-tab", "title": "Feedback"}],
                "can": {"send_briefs": True, "reach": ["api.github.com"]},
            }
        )
        self.assertEqual(lines[0], "Shows up in `project-tab`: Feedback")
        self.assertIn("Reach 1 website: `api.github.com`", lines)


def make_tarball(files, links=()):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


class TarballTest(unittest.TestCase):
    def test_reads_and_extracts_only_the_plugin_folder(self):
        import pr_check

        data = make_tarball(
            {
                "repo-abc/README.md": "top",
                "repo-abc/plug/plugin.yaml": "name: plug\n",
                "repo-abc/plug/on.md": "turn on",
                "repo-abc/plug/../../escape.txt": "no",
            },
            links=[("repo-abc/plug/link", "/etc/passwd")],
        )
        original = (listlib.download_tarball, pr_check.download_tarball)
        listlib.download_tarball = pr_check.download_tarball = lambda repo, commit: data
        try:
            self.assertEqual(listlib.fetch_plugin_yaml("a/b", "a" * 40, "plug"), "name: plug\n")
            with self.assertRaises(listlib.ListError):
                listlib.fetch_plugin_yaml("a/b", "a" * 40, ".")
            with tempfile.TemporaryDirectory() as tmp:
                pr_check.extract_plugin("a/b", "a" * 40, "plug", tmp)
                self.assertEqual(sorted(os.listdir(tmp)), ["on.md", "plugin.yaml"])
                self.assertFalse(os.path.exists(os.path.join(os.path.dirname(tmp), "escape.txt")))
        finally:
            listlib.download_tarball, pr_check.download_tarball = original


class SampleIndexTest(unittest.TestCase):
    def test_sample_has_index_shape(self):
        with open(os.path.join(ROOT, "examples", "index.sample.json")) as handle:
            index = json.load(handle)
        self.assertEqual(set(index), {"version", "generated_at", "categories", "plugins"})
        ids = {c["id"] for c in index["categories"]}
        names = [p["name"] for p in index["plugins"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(6 <= len(names) <= 8)
        self.assertGreaterEqual(len({p["category"] for p in index["plugins"]}), 3)
        self.assertNotIn("namlo", names)
        for plugin in index["plugins"]:
            listlib.check_entry({k: plugin[k] for k in listlib.ENTRY_KEYS}, plugin["name"])
            listlib.check_plugin(plugin, plugin["name"], ids, plugin["name"])

    def test_categories_file_loads(self):
        self.assertTrue(listlib.load_categories(ROOT))


if __name__ == "__main__":
    unittest.main()
