"""Tests for the static showcase builder/validator (scripts/build_site.py)."""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("build_site", REPO / "scripts" / "build_site.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_site"] = module
    spec.loader.exec_module(module)
    return module


build_site = _load()

GOOD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>t</title>
<link rel="stylesheet" href="styles.css"></head><body>
<h2 id="top">x</h2><a href="#top">top</a>
<img src="assets/a.png" alt="a picture">
<a href="https://github.com/gladwynL/CloudPulse">source</a>
<p>This is a static showcase. It is not hosted publicly as a live backend.</p>
</body></html>"""


@pytest.fixture
def good_site(tmp_path: Path) -> Path:
    site = tmp_path / "_site"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text(GOOD_PAGE, encoding="utf-8")
    (site / "styles.css").write_text("body { color: red; }", encoding="utf-8")
    (site / "assets" / "a.png").write_bytes(b"\x89PNG")
    return site


def problems_with(site: Path, page: str | None = None, extra: dict[str, str] | None = None):
    if page is not None:
        (site / "index.html").write_text(page, encoding="utf-8")
    for name, content in (extra or {}).items():
        (site / name).write_text(content, encoding="utf-8")
    return build_site.validate(site)


def test_a_well_formed_site_passes(good_site: Path) -> None:
    assert build_site.validate(good_site) == []


def test_a_missing_index_is_reported(tmp_path: Path) -> None:
    assert build_site.validate(tmp_path) == ["index.html is missing"]


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('<img src="assets/missing.png" alt="x">', "broken reference"),
        ('<a href="#nowhere">x</a>', "no matching id"),
        ('<img src="assets/a.png">', "no alt"),
        ('<script src="app.js"></script>', "<script>"),
        ('<a href="https://evil.example/x">x</a>', "external reference"),
        ('<a href="http://localhost:8000/api/v1/overview">api</a>', "external reference"),
        ('<img src="/assets/a.png" alt="x">', "root-relative"),
        ('<button onclick="go()">x</button>', "event handler"),
        ('<meta http-equiv="refresh" content="0;url=https://x.example">', "meta refresh"),
        ("<p>fetch('/api')</p>", "network calls"),
        ("<p>POSTGRES_PASSWORD=hunter2hunter2</p>", "password"),
        ("<p>token ghp_abcdefghijklmnopqrstuvwxyz0123456789</p>", "token"),
        ("<p>C:\\Users\\someone\\project</p>", "Windows user path"),
        ("<p>host 192.168.1.20 is private</p>", "private IP"),
        ('<a href="https://github.com/gladwynL/CloudPulse?token=abc">x</a>', "credential in a URL"),
        ("<p>BACKEND_TESTS tests</p>", "placeholder"),
    ],
)
def test_validation_catches_unsafe_or_broken_content(
    good_site: Path, snippet: str, expected: str
) -> None:
    page = GOOD_PAGE.replace("</body>", f"{snippet}</body>")
    found = problems_with(good_site, page)
    assert any(expected in problem for problem in found), found


def test_the_honesty_notice_is_mandatory(good_site: Path) -> None:
    page = GOOD_PAGE.replace("static showcase", "marketing page")
    assert any("required notice" in p for p in problems_with(good_site, page))


def test_only_allowlisted_file_types_may_be_published(good_site: Path) -> None:
    found = problems_with(good_site, extra={"backup.dump": "x", ".env": "A=1", "server.py": "x"})
    assert sum("not allowed" in p for p in found) >= 2 and any("hidden" in p for p in found)


def test_oversized_images_are_rejected(good_site: Path) -> None:
    (good_site / "assets" / "a.png").write_bytes(b"0" * (build_site.MAX_IMAGE_BYTES + 1))
    assert any("image larger" in p for p in build_site.validate(good_site))


def test_css_may_not_import_or_load_remote_resources(good_site: Path) -> None:
    css = (
        '@import url("https://fonts.example/x.css"); a { background: url(https://x.example/i.png) }'
    )
    found = problems_with(good_site, extra={"styles.css": css})
    assert any("@import" in p for p in found) and any("must be a relative file" in p for p in found)


def test_build_refuses_to_wipe_a_folder_that_is_not_a_site_build(tmp_path: Path) -> None:
    victim = tmp_path / "important"
    victim.mkdir()
    (victim / "data.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        build_site.build(victim)
    assert (victim / "data.txt").read_text() == "keep me"


def test_the_real_site_builds_and_validates(tmp_path: Path) -> None:
    """The committed site plus the committed screenshots must always produce a valid build."""
    out = tmp_path / "_site"
    build_site.build(out)
    assert build_site.validate(out) == []
    # Only the showcase and screenshots are published; never repository files.
    published = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert "index.html" in published and "styles.css" in published
    assert all(
        not name.startswith(("backend", "frontend", ".github", "docs/")) for name in published
    )
    assert not any(name.endswith((".py", ".env", ".dump", ".yml", ".sql")) for name in published)


def test_every_documentation_screenshot_is_reviewed_small_and_png() -> None:
    shots = sorted((REPO / "docs" / "screenshots").glob("*"))
    assert shots, "expected screenshots"
    for shot in shots:
        assert shot.suffix == ".png", shot.name
        assert shot.stat().st_size <= build_site.MAX_IMAGE_BYTES, shot.name


def test_copying_the_real_site_does_not_touch_the_source(tmp_path: Path) -> None:
    before = sorted(p.name for p in (REPO / "site").iterdir())
    build_site.build(tmp_path / "_site")
    assert sorted(p.name for p in (REPO / "site").iterdir()) == before
    shutil.rmtree(tmp_path / "_site")
