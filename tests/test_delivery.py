"""Regression checks at the Git / HTML / browser-engine boundaries."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from vibecheck import blast, cli, project, repo, report, scan
from test_vibecheck import build, make_scan


class ReportDocument(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.external = []
        self.in_data = False
        self.data = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append(attrs)
            self.in_data = attrs.get("id") == "vc-data"
        if (tag == "script" and "src" in attrs) or (tag == "link" and attrs.get("rel") in ("stylesheet", "preconnect")):
            self.external.append(attrs)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data += data


class Delivery(unittest.TestCase):
    def test_package_member_imports_preserve_module_and_test_edges(self):
        files = {"pkg/__init__.py": "", "pkg/a.py": "def run(): pass\n",
                 "pkg/b.py": "from . import a\n", "tests/test_a.py": "from pkg import a, b\n"}
        snap = repo.Snapshot(Path("fixture"), None, files=files, all_paths=list(files))
        proj = project.load(snap)
        self.assertIn(("tests/test_a.py", "pkg/a.py"), proj.edges)
        self.assertIn(("tests/test_a.py", "pkg/b.py"), proj.edges)
        self.assertIn(("pkg/b.py", "pkg/a.py"), proj.edges)

    def test_parse_failure_is_visible_in_findings_and_scan_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "broken.py").write_text("def broken(:\n")
            result = scan.run(root)
            failures = [f for f in result.findings if f["id"] == "parse.incomplete"]
            self.assertEqual(failures[0]["evidence"][0]["file"], "broken.py")
            self.assertEqual(scan.to_json(result, root)["stats"]["parse_errors"][0]["file"], "broken.py")

    def test_report_is_offline_and_repository_content_cannot_escape(self):
        payload = '</title><script>alert("bad")</script><!-- & café'
        result = {"repo": {"name": payload}, "evidence": payload}
        with tempfile.TemporaryDirectory() as d:
            output = report.render(result, Path(d) / "report.html").read_text()
        parser = ReportDocument()
        parser.feed(output)
        self.assertEqual(json.loads(parser.data), result)
        self.assertEqual(len(parser.scripts), 3)
        self.assertEqual(parser.external, [])
        self.assertIn('&lt;/title&gt;', output)
        self.assertNotIn('/*{{', output)
        self.assertNotIn('\x00', output)

    def test_invalid_directory_returns_error_status(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code = cli.main(["scan", "/this/directory/does/not/exist"])
        self.assertEqual(code, 2)
        self.assertIn("not a repository directory", errors.getvalue())


class GitSnapshots(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        repo.git(self.source, "init", "-b", "main")
        repo.git(self.source, "config", "user.email", "test@example.invalid")
        repo.git(self.source, "config", "user.name", "Fixture")
        (self.source / "one.py").write_text("VALUE = 1\n")
        self.commit()

    def commit(self):
        repo.git(self.source, "add", ".")
        repo.git(self.source, "commit", "-qm", "fixture")

    def test_reused_url_clone_scans_fetched_commit_without_changing_checkout(self):
        dest = repo.clone(str(self.source), self.root / "cache")
        original = repo.resolve_ref(dest)
        (self.source / "two.py").write_text("VALUE = 2\n")
        self.commit()
        reused = repo.clone(str(self.source), self.root / "cache")
        self.assertEqual(reused, dest)
        self.assertEqual(repo.resolve_ref(dest), original)
        current = repo.remote_ref(dest)
        self.assertEqual(current, repo.resolve_ref(self.source))
        self.assertIn("two.py", repo.snapshot(dest, current).files)
        self.assertEqual(repo.remote_ref(dest, "main"), current)
        self.assertEqual(repo.remote_ref(dest, original), original)

    def test_fetch_failure_does_not_silently_reuse_stale_results(self):
        repo.clone(str(self.source), self.root / "cache")
        with patch("vibecheck.repo.git", side_effect=RuntimeError("fetch failed")):
            with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                repo.clone(str(self.source), self.root / "cache")

    def test_tracked_newline_path_does_not_desynchronize_batch_reads(self):
        (self.source / "new\nline.py").write_text("ODD = True\n")
        (self.source / "z.py").write_text("LAST = True\n")
        self.commit()
        snap = repo.snapshot(self.source)
        self.assertEqual(snap.files["new\nline.py"], "ODD = True\n")
        self.assertEqual(snap.files["z.py"], "LAST = True\n")


@unittest.skipUnless(shutil.which("node"), "Node is required for Python/browser parity checks")
class BrowserEngineParity(unittest.TestCase):
    def test_browser_and_python_agree_on_representative_requests(self):
        s = make_scan(*build())
        data = scan.to_json(s, s.snap.root)
        requests = ["replace the save system", "rename wardrobe positions", "add multiplayer", "let Wardrobe call UI", "", "quantum bananas"]
        engine = Path(__file__).resolve().parents[1] / "vibecheck/assets/blast.js"
        script = '''const fs = require('fs'); const {createBlast} = require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(input.requests.map(q=>createBlast(input.data).run(q))));'''
        output = subprocess.run(["node", "-e", script, str(engine)], input=json.dumps({"data": data, "requests": requests}), text=True, capture_output=True, check=True)
        actual = json.loads(output.stdout)
        for request, browser_result in zip(requests, actual):
            with self.subTest(request=request):
                expected = blast.run(s, request)
                self.assertEqual([v["path"] for v in browser_result["seeds"]], [v["path"] for v in expected["seeds"]])
                self.assertEqual([(v["path"], v["kind"]) for v in browser_result["impacted"]], [(v["path"], v["kind"]) for v in expected["impacted"]])
                self.assertEqual(browser_result["pack"], expected["pack"])
