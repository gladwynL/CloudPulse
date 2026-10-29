#!/usr/bin/env python3
"""Assemble and validate the static showcase (`site/`) for GitHub Pages. Stdlib only.

    python scripts/build_site.py                # builds ./_site and validates it
    python scripts/build_site.py --out /tmp/x   # (the output folder must be named _site* or be new)

The published artifact is exactly the output folder: `site/` plus the real dashboard screenshots
from `docs/screenshots/` (copied to `assets/`). Nothing else from the repository can end up in it.
Validation fails the build when a link or image is broken, when the page references anything but
its own files and the project's GitHub URLs, contains scripts, or contains something that looks
like a secret or a private address, or when the "static showcase, not publicly hosted" notice is
missing. Exit code 0 only if everything passes.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "site"
SCREENSHOT_DIR = REPO_ROOT / "docs" / "screenshots"
DEFAULT_OUT = REPO_ROOT / "_site"

ALLOWED_EXTENSIONS = {".html", ".css", ".svg", ".png", ".ico", ".txt"}
ALLOWED_ABSOLUTE_PREFIXES = ("https://github.com/gladwynL/CloudPulse",)
MAX_IMAGE_BYTES = 700_000
MAX_SITE_BYTES = 4_000_000
REQUIRED_PHRASES = ("static showcase", "not hosted publicly")

SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(postgres_)?password\s*[=:]"), "a password assignment"),
    (re.compile(r"(?i)(ghp_|github_pat_|AKIA[0-9A-Z]{12}|xox[bap]-)"), "a token"),
    (re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"), "a private key"),
    (re.compile(r"[A-Za-z]:\\Users\\"), "a Windows user path"),
    (re.compile(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+(\.\d+){1,2}\b"), "a private IP address"),
    (re.compile(r"\?[^\s\"'<>]*(token|secret|key|password)=", re.IGNORECASE), "a credential in a URL"),
]


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []  # (attribute, value)
        self.ids: set[str] = set()
        self.tags: list[str] = []
        self.event_handlers: list[str] = []
        self.images_without_alt: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = {name: (value or "") for name, value in attrs}
        if "id" in values:
            self.ids.add(values["id"])
        for name in ("src", "href", "poster", "action"):
            if name in values:
                self.refs.append((name, values[name]))
        for name in values:
            if name.startswith("on"):
                self.event_handlers.append(f"<{tag} {name}>")
        if tag == "img" and "alt" not in values:
            self.images_without_alt.append(values.get("src", "?"))
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.refs.append(("meta-refresh", values.get("content", "")))


def build(out: Path) -> None:
    out = out.resolve()
    if out.exists():
        # Never wipe an arbitrary folder: only a previous build output may be replaced.
        if not out.name.startswith("_site") or not out.is_dir():
            raise SystemExit(f"refusing to replace {out}: not a previous _site* build folder")
        shutil.rmtree(out)
    shutil.copytree(SITE_DIR, out)
    assets = out / "assets"
    assets.mkdir(exist_ok=True)
    if SCREENSHOT_DIR.is_dir():
        for image in sorted(SCREENSHOT_DIR.glob("*.png")):
            shutil.copyfile(image, assets / image.name)


def _local_target(base: Path, root: Path, ref: str) -> Path | None:
    """Resolve a relative reference to a file inside ``root``; None if it points outside."""
    parts = urlsplit(ref)
    path = unquote(parts.path)
    target = (base.parent / path).resolve() if path else base.resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def validate(out: Path) -> list[str]:
    problems: list[str] = []
    index = out / "index.html"
    if not index.is_file():
        return ["index.html is missing"]

    files = [p for p in out.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if total > MAX_SITE_BYTES:
        problems.append(f"site is {total:,} bytes; the limit is {MAX_SITE_BYTES:,}")
    for path in files:
        relative = path.relative_to(out).as_posix()
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            problems.append(f"{relative}: file type not allowed in the published site")
        if path.name.startswith(".") or "node_modules" in path.parts:
            problems.append(f"{relative}: hidden or dependency file")
        if path.suffix.lower() == ".png" and path.stat().st_size > MAX_IMAGE_BYTES:
            problems.append(f"{relative}: image larger than {MAX_IMAGE_BYTES:,} bytes")

    for path in (p for p in files if p.suffix.lower() in {".html", ".css", ".svg", ".txt"}):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(out).as_posix()
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{relative}: contains {label}")

    for page in (p for p in files if p.suffix.lower() == ".html"):
        text = page.read_text(encoding="utf-8")
        relative = page.relative_to(out).as_posix()
        collector = _Collector()
        collector.feed(text)

        if "script" in collector.tags:
            problems.append(f"{relative}: contains a <script> (the showcase is script-free)")
        for handler in collector.event_handlers:
            problems.append(f"{relative}: inline event handler {handler}")
        for source in collector.images_without_alt:
            problems.append(f"{relative}: image {source} has no alt attribute")
        if re.search(r"\b(fetch|XMLHttpRequest|WebSocket|sendBeacon)\b", text):
            problems.append(f"{relative}: appears to make network calls")

        for attribute, ref in collector.refs:
            ref = ref.strip()
            if attribute == "meta-refresh":
                problems.append(f"{relative}: meta refresh is not allowed")
            elif ref.startswith("#"):
                if ref != "#" and ref[1:] not in collector.ids:
                    problems.append(f"{relative}: anchor {ref} has no matching id")
            elif re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref) or ref.startswith("//"):
                if not ref.startswith(ALLOWED_ABSOLUTE_PREFIXES):
                    problems.append(f"{relative}: external reference {ref} is not allowed")
            elif ref.startswith("/"):
                problems.append(
                    f"{relative}: root-relative URL {ref} would break under /CloudPulse/; "
                    "use a relative path"
                )
            else:
                target = _local_target(page, out, ref)
                if target is None or not target.is_file():
                    problems.append(f"{relative}: broken reference {ref}")

        if re.search(r"\b[A-Z]{3,}(_[A-Z]+)*_TESTS\b", text):
            problems.append(f"{relative}: an unfilled placeholder (such as BACKEND_TESTS) remains")

        lowered = text.lower()
        for phrase in REQUIRED_PHRASES:
            if phrase not in lowered:
                problems.append(f"{relative}: the required notice \"{phrase}\" is missing")

    for stylesheet in (p for p in files if p.suffix.lower() == ".css"):
        css = stylesheet.read_text(encoding="utf-8")
        for match in re.finditer(r"url\(\s*['\"]?([^'\")]+)", css):
            ref = match.group(1)
            if ref.startswith(("data:", "#")):
                continue
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref) or ref.startswith("/"):
                problems.append(f"{stylesheet.name}: url({ref}) must be a relative file")
            else:
                target = _local_target(stylesheet, out, ref)
                if target is None or not target.is_file():
                    problems.append(f"{stylesheet.name}: broken url({ref})")
        if "@import" in css:
            problems.append(f"{stylesheet.name}: @import is not allowed")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check-only", action="store_true", help="validate an existing build")
    args = parser.parse_args(argv)

    if not args.check_only:
        build(args.out)
    problems = validate(args.out)
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if problems:
        return 1
    count = sum(1 for p in args.out.rglob("*") if p.is_file())
    print(f"site OK: {count} files in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
