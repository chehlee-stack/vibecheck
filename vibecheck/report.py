"""The report: one self-contained HTML file, the scan's JSON embedded in it."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")


HEAD = ('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')


def render(result: dict, out: Path, standalone: bool = True) -> Path:
    """Write the report. `standalone` wraps the page as a complete document for local viewing;
    without it the file is the body-only form an Artifact publish expects."""
    data = json.dumps(result, separators=(",", ":"), default=str)
    data = data.replace("</", "<\\/").replace("<!--", "<\\!--")   # nothing in the data can close the script tag
    html = TEMPLATE.read_text(encoding="utf-8")
    title = "%s VibeCheck" % result["repo"]["name"]
    html = html.replace("{{TITLE}}", title).replace("/*{{DATA}}*/", data)
    if standalone:
        html = HEAD + html + "\n</html>\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
