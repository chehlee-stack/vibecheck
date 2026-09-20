"""The report: one self-contained HTML file, the scan's JSON embedded in it."""
from __future__ import annotations

import json
import html as html_lib
import re
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
ASSETS = Path(__file__).with_name("assets")


HEAD = ('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')


def render(result: dict, out: Path, standalone: bool = True) -> Path:
    """Write the report. `standalone` wraps the page as a complete document for local viewing;
    without it the file is the body-only form an Artifact publish expects."""
    data = json.dumps(result, separators=(",", ":"), default=str)
    # JSON escapes preserve the payload and prevent HTML's script parser from
    # interpreting closing tags or comment openers in repository content.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = TEMPLATE.read_text(encoding="utf-8")
    replacements = {}
    for marker, filename in (("STYLE", "report.css"), ("BLAST", "blast.js"), ("SCRIPT", "report.js")):
        replacements["/*{{%s}}*/" % marker] = (ASSETS / filename).read_text(encoding="utf-8")
    title = html_lib.escape("%s · GCAA" % result["repo"]["name"])
    replacements.update({"{{TITLE}}": title, "/*{{DATA}}*/": data})
    # A single pass cannot reinterpret placeholder text inside repository data.
    pattern = "|".join(re.escape(key) for key in replacements)
    html = re.sub(pattern, lambda match: replacements[match.group()], html)
    if standalone:
        html = HEAD + html + "\n</html>\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
