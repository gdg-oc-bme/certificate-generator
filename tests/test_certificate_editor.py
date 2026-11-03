from pathlib import Path
import pytest
from PIL import Image, ImageDraw, ImageFont
import certificateEditor as ce
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


def name_layout(name, scale=1):
    draw = ImageDraw.Draw(Image.new("RGB", (round(2048 * scale), round(1447 * scale))))
    lines = ce._layout_name(
        draw, name, round(115 * scale), round(485 * scale),
        round(1050 * scale), round(380 * scale), round(25 * scale),
        ce.name_font_path, ce.arabic_name_font_path,
        round(165 * scale), round(95 * scale),
    )
    return draw, lines


@pytest.mark.parametrize("name", [
    "أحمد محمد", "أحمد محمد عبد الرحمن صالح محمود",
    "محمد أحمد Alexander Johnson", "Alexander Johnson محمد أحمد",
    "محمد Ahmed أحمد Johnson",
    "Christopher Jonathan Montgomery Alexander Johnson", "Alex Johnson",
])
@pytest.mark.parametrize("scale", [0.75, 1, 2])
def test_name_ink_stays_inside_name_area(name, scale):
    draw, lines = name_layout(name, scale)
    boxes = [draw.textbbox(position, text, font=font) for text, font, position in lines]
    assert boxes
    for left, top, right, bottom in boxes:
        assert left >= round(90 * scale)
        assert right <= round(1165 * scale)
        assert top >= round(485 * scale)
        assert bottom <= round(865 * scale)
        assert bottom < round(ce.EVENT_Y_R * scale)
    for previous, following in zip(boxes, boxes[1:]):
        assert following[1] - previous[3] >= round(25 * scale)


def test_short_arabic_stays_one_shaped_rtl_line():
    _, lines = name_layout("أحمد محمد")
    assert len(lines) == 1
    assert lines[0][0] == ce._shape_arabic_text("أحمد محمد")
    assert lines[0][1].layout_engine == ImageFont.Layout.BASIC


def test_mixed_name_keeps_script_runs_and_latin_word_order():
    _, lines = name_layout("محمد أحمد Alexander Johnson")
    assert [line[0] for line in lines] == [
        ce._shape_arabic_text("محمد أحمد"), "Alexander Johnson",
    ]
    assert lines[0][1].layout_engine == ImageFont.Layout.BASIC


def test_alex_johnson_preserves_existing_fonts_and_positions():
    draw, lines = name_layout("Alex Johnson")
    font = ce._load_font(ce.name_font_path, 165)
    box = draw.textbbox((115, 485), "Alex", font=font)
    assert [line[0] for line in lines] == ["Alex", "Johnson"]
    assert [line[1].size for line in lines] == [165, 165]
    assert [line[2] for line in lines] == [(115, 485), (115, 485 + box[3] - box[1] + 25)]


def test_unbreakable_name_fails_explicitly_before_drawing():
    with pytest.raises(ValueError, match="name.*fit"):
        name_layout("W" * 200)


@pytest.mark.parametrize("name", [
    "أحمد محمد", "محمد أحمد Alexander Johnson", "Alex Johnson",
    "أحمد محمد عبد الرحمن صالح محمود",
    "Christopher Jonathan Montgomery Alexander Johnson",
])
def test_drawn_name_pixels_do_not_enter_illustration_or_event_area(name):
    image = Image.new("RGB", (2048, 1447), "white")
    draw = ImageDraw.Draw(image)
    ce._draw_name(draw, name, 115, 485, 1050, 25, ce.name_font_path,
                  ce.arabic_name_font_path, 165, 95, 380)
    ink = image.convert("L").point(lambda p: 255 - p)
    left, top, right, bottom = ink.getbbox()
    assert left >= 90 and right <= 1165
    assert top >= 485 and bottom <= 865
    assert ink.crop((1165, 0, 2048, 1447)).getbbox() is None
    assert ink.crop((0, 865, 2048, 1447)).getbbox() is None


def test_exceptional_name_never_partially_draws():
    image = Image.new("RGB", (2048, 1447), "white")
    with pytest.raises(ValueError, match="name.*fit"):
        ce._draw_name(ImageDraw.Draw(image), "W" * 200, 115, 485, 1050, 25,
                      ce.name_font_path, ce.arabic_name_font_path, 165, 95, 380)
    assert image.getextrema() == ((255, 255),) * 3


def test_many_short_script_runs_can_share_a_bidi_line_when_rows_would_overflow():
    name = "محمد Ahmed أحمد Johnson"
    _, lines = name_layout(name)
    # Four separate script rows exceed the available height. Two mixed rows
    # preserve logical word order without forcing an unreadably small font.
    assert [line[0] for line in lines] == [
        ce._shape_arabic_text("محمد Ahmed"),
        ce._shape_arabic_text("أحمد Johnson"),
    ]


def test_insufficient_vertical_space_is_an_explicit_error():
    draw = ImageDraw.Draw(Image.new("RGB", (2048, 1447)))
    with pytest.raises(ValueError, match="name.*fit"):
        ce._layout_name(draw, "أحمد محمد", 115, 485, 1050, 30, 25,
                        ce.name_font_path, ce.arabic_name_font_path, 165, 95)


@pytest.mark.parametrize("name,max_fonts", [
    ("أحمد محمد عبد الرحمن صالح محمود", 16),
    ("محمد أحمد Alexander Johnson", 24),
])
def test_layout_reuses_shaping_and_bounds_font_search(name, max_fonts, monkeypatch):
    from unittest.mock import Mock
    load = Mock(wraps=ImageFont.truetype)
    shape = Mock(wraps=ce._shape_arabic_text)
    monkeypatch.setattr(ImageFont, "truetype", load)
    monkeypatch.setattr(ce, "_shape_arabic_text", shape)
    _, lines = name_layout(name, 1.714)
    assert len(lines) == 2
    assert load.call_count <= max_fonts
    inputs = [call.args[0] for call in shape.call_args_list]
    assert len(inputs) == len(set(inputs))
    assert shape.call_count <= 32
