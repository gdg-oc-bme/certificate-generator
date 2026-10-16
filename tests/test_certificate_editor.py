from pathlib import Path
from certificateEditor import (
    _contains_arabic,
    _safe_filename,
    _split_name_two_lines,
    _pretty_date_ddmmyyyy,
    _unique_output_path,
    resource_path,
)


def test_safe_filename_falls_back_when_empty():
    result = _safe_filename("   ", 7)
    assert result == "certificate_7"


def test_safe_filename_removes_trailing_dots_and_spaces():
    result = _safe_filename("John Doe . ")
    assert result == "John Doe"


def test_safe_filename_collapses_multiple_spaces():
    result = _safe_filename("John     Doe")
    assert result == "John Doe"


def test_unique_output_path_returns_plain_name_when_unused(tmp_path):
    result = _unique_output_path(tmp_path, "John Doe", 0)
    assert result == tmp_path / "John Doe.jpg"


def test_unique_output_path_adds_counter_when_name_exists(tmp_path):
    first = tmp_path / "John Doe.jpg"
    first.write_text("dummy")

    result = _unique_output_path(tmp_path, "John Doe", 0)
    assert result == tmp_path / "John Doe (2).jpg"


def test_unique_output_path_skips_to_next_available_counter(tmp_path):
    (tmp_path / "John Doe.jpg").write_text("dummy")
    (tmp_path / "John Doe (2).jpg").write_text("dummy")

    result = _unique_output_path(tmp_path, "John Doe", 0)
    assert result == tmp_path / "John Doe (3).jpg"


def test_resource_path_returns_existing_project_path_for_relative_file():
    result = resource_path("certificateEditor.py")
    assert isinstance(result, Path)
    assert result.exists()


def test_pretty_date_raises_on_bad_input():
    try:
        _pretty_date_ddmmyyyy("2026-03-27")
        assert False, "Expected ValueError"
    except ValueError:
        assert True


def test_contains_arabic_false_for_latin_name():
    assert _contains_arabic("John Doe") is False


def test_contains_arabic_true_for_arabic_name():
    assert _contains_arabic("أحمد صالح محمود") is True


def test_safe_filename_removes_invalid_characters():
    result = _safe_filename("John:Doe/Name?")
    assert result == "John_Doe_Name_"


def test_safe_filename_keeps_unicode_name():
    result = _safe_filename("أحمد صالح محمود")
    assert result == "أحمد صالح محمود"


def test_split_name_two_lines_single_word():
    first, second = _split_name_two_lines("John")
    assert first == "John"
    assert second == ""


def test_split_name_two_lines_multiple_words():
    first, second = _split_name_two_lines("John Michael Doe")
    assert first == "John"
    assert second == "Michael Doe"


def test_pretty_date_formats_correctly():
    assert _pretty_date_ddmmyyyy("27/03/2026") == "March 27, 2026"