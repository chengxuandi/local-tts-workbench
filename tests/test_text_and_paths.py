from __future__ import annotations

import pytest

from app.config import project_directory, safe_filename_component, validate_project_slug
from app.services.generation import effective_text, estimate_cost, utf8_size


@pytest.mark.parametrize(
    "text",
    ["ASCII", "中文", "中文 mixed", "emoji 😀", "[sigh] 好吧"],
)
def test_utf8_count(text):
    assert utf8_size(text) == len(text.encode("utf-8"))


def test_effective_text_keeps_local_cues():
    result = effective_text("我没有生气。[sigh] 继续。", "压着怒气", "s2.1-pro")
    assert result == "[压着怒气]\n我没有生气。[sigh] 继续。"
    assert estimate_cost(824, 15) == pytest.approx(0.01236)


@pytest.mark.parametrize(
    "slug", ["../", "../../x", "C:\\temp", "/absolute", "CON", "PRN", "a/b", "a?b", "x\x01"]
)
def test_project_slug_rejects_unsafe(slug):
    with pytest.raises(ValueError):
        validate_project_slug(slug)


@pytest.mark.parametrize("slug", ["chapter_01", "中文章节", "chapter-2"])
def test_project_slug_allows_safe_names(tmp_path, slug):
    assert validate_project_slug(slug) == slug
    root = project_directory(tmp_path, slug)
    assert root.parent == (tmp_path / "projects").resolve()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alice", "Alice"),
        ("中文角色", "中文角色"),
        ("Alice/Test", "Alice_Test"),
        ("Alice:Test", "Alice_Test"),
        ('<>:"/\\|?*', "character-9"),
    ],
)
def test_safe_character_filename(name, expected):
    assert safe_filename_component(name, "character-9") == expected
