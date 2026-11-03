from pathlib import Path
from io import BytesIO

import pytest
from PIL import Image, ImageChops, ImageDraw, PdfParser

import certificateEditor as ce


def test_edit_certificate_creates_output_file(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = ce.edit_certificate(
        template_path="template_certificate_no_line.jpg",
        attendees_list=["John Doe"],
        eventTitle="Hardware Workshop",
        eventDate="27/03/2026",
        name_font_path=ce.name_font_path,
        regular_font_path=ce.regular_font_path,
        save_dir=str(output_dir),
    )

    assert len(result) == 1
    output_file = Path(result[0])
    assert output_file.exists()
    assert output_file.suffix.lower() == ".jpg"


def read_pdf_output(path):
    # Pillow's parser is sufficient for the single-image PDFs its encoder writes.
    with PdfParser.PdfParser(filename=str(path)) as pdf:
        assert len(pdf.pages) == 1
        page = pdf.read_indirect(pdf.pages[0])
        image = pdf.read_indirect(page[b"Resources"][b"XObject"][b"image"])
        with Image.open(BytesIO(image.buf)) as embedded:
            return page[b"MediaBox"], embedded.convert("RGB")


@pytest.mark.parametrize("size", [(2048, 1447), (3508, 2480), (4882, 3425)])
def test_pdf_is_a4_landscape_for_different_template_sizes(tmp_path, size):
    source = Image.new("RGB", size, "lightblue")

    paths = ce._save_certificate_outputs(source, tmp_path, "Sample Participant", 0, "PDF")

    assert len(paths) == 1
    box, embedded = read_pdf_output(paths[0])
    assert box == pytest.approx([0, 0, 297 / 25.4 * 72, 210 / 25.4 * 72], abs=0.1)
    assert embedded.size == (3508, 2480)
    assert source.size == size


def test_batch_preflight_rejects_before_any_save_and_preserves_existing(tmp_path, monkeypatch):
    from unittest.mock import Mock
    existing = tmp_path / "existing.jpg"
    existing.write_bytes(b"existing synthetic output")
    save = Mock()
    monkeypatch.setattr(ce, "_save_certificate_outputs", save)
    invalid = "W" * 200
    with pytest.raises(ce.CertificateLayoutError) as caught:
        ce.edit_certificate(
            "template_certificate_no_line.jpg", ["Alex Johnson", invalid, "Jane Smith"],
            "", "", ce.name_font_path, ce.regular_font_path, str(tmp_path),
            participant_rows=[2, 8, 9],
        )
    save.assert_not_called()
    assert caught.value.participant == invalid
    assert caught.value.participant_index == 2
    assert caught.value.csv_row == 8
    assert "No certificates" in str(caught.value)
    assert existing.read_bytes() == b"existing synthetic output"
    assert list(tmp_path.iterdir()) == [existing]


def test_valid_batch_reuses_preflight_layouts_in_order(tmp_path, monkeypatch):
    from unittest.mock import Mock
    layout = Mock(wraps=ce._layout_name)
    monkeypatch.setattr(ce, "_layout_name", layout)
    names = ["Alex Johnson", "أحمد محمد", "Alex Johnson"]
    paths = ce.edit_certificate("template_certificate_no_line.jpg", names, "", "",
                                ce.name_font_path, ce.regular_font_path, str(tmp_path))
    assert layout.call_count == 2  # duplicate names reuse this batch's layout
    assert [Path(p).stem for p in paths] == ["Alex Johnson", "أحمد محمد", "Alex Johnson (2)"]
    assert all(Path(p).exists() for p in paths)


def test_runtime_failure_reports_known_completed_outputs(tmp_path, monkeypatch):
    original = ce._save_certificate_outputs
    calls = []

    def save(**kwargs):
        calls.append(kwargs["full_name"])
        if len(calls) == 2:
            raise OSError("synthetic disk failure")
        return original(**kwargs)

    monkeypatch.setattr(ce, "_save_certificate_outputs", save)
    with pytest.raises(ce.CertificateGenerationError) as caught:
        ce.edit_certificate("template_certificate_no_line.jpg", ["Alex Johnson", "Jane Smith"],
                            "", "", ce.name_font_path, ce.regular_font_path, str(tmp_path))
    assert caught.value.completed_count == 1
    assert len(caught.value.completed_paths) == 1
    assert Path(caught.value.completed_paths[0]).exists()
    assert "synthetic disk failure" in str(caught.value)
    assert "partial files" in str(caught.value)


@pytest.mark.parametrize("size", [(1200, 400), (400, 1200), (800, 800)])
def test_pdf_centers_complete_design_without_distortion(tmp_path, size):
    source = Image.new("RGB", size, (30, 80, 180))
    draw = ImageDraw.Draw(source)
    width, height = size
    colors = [(240, 20, 20), (20, 200, 20), (240, 220, 20), (200, 20, 200)]
    corners = [(0, 0), (width * 3 // 4, 0), (0, height * 3 // 4),
               (width * 3 // 4, height * 3 // 4)]
    for (x, y), color in zip(corners, colors):
        draw.rectangle((x, y, x + width // 4 - 1, y + height // 4 - 1), fill=color)

    paths = ce._save_certificate_outputs(source, tmp_path, "Sample Participant", 0, "PDF")
    _, embedded = read_pdf_output(paths[0])

    difference = ImageChops.difference(embedded, Image.new("RGB", embedded.size, "white"))
    bounds = difference.convert("L").point(lambda value: 255 if value > 35 else 0).getbbox()
    scale = min(3508 / width, 2480 / height)
    fitted_width, fitted_height = round(width * scale), round(height * scale)
    left, top = (3508 - fitted_width) // 2, (2480 - fitted_height) // 2
    assert bounds == pytest.approx(
        (left, top, left + fitted_width, top + fitted_height), abs=3
    )
    # Every corner survives, in its original position and proportions.
    for (x, y), color in zip(corners, colors):
        point = (left + round((x + width / 8) * scale),
                 top + round((y + height / 8) * scale))
        assert embedded.getpixel(point) == pytest.approx(color, abs=8)
    assert source.size == size
    assert source.getpixel((width // 2, height // 2)) == (30, 80, 180)


@pytest.mark.parametrize("output_format", ["JPG", "Both"])
def test_jpg_keeps_original_dimensions_and_encoding(tmp_path, output_format):
    source = Image.new("RGB", (900, 600), "lightblue")
    expected = BytesIO()
    source.save(expected, format="JPEG", quality=95)

    paths = ce._save_certificate_outputs(source, tmp_path, "Sample Participant", 0, output_format)

    jpg_path = next(Path(path) for path in paths if Path(path).suffix == ".jpg")
    assert jpg_path.read_bytes() == expected.getvalue()
    with Image.open(jpg_path) as jpg:
        assert jpg.size == source.size
    assert {Path(path).suffix for path in paths} == (
        {".jpg", ".pdf"} if output_format == "Both" else {".jpg"}
    )
    if output_format == "Both":
        pdf_path = next(Path(path) for path in paths if Path(path).suffix == ".pdf")
        assert pdf_path.stem == jpg_path.stem
        box, _ = read_pdf_output(pdf_path)
        assert box[2:] == pytest.approx([297 / 25.4 * 72, 210 / 25.4 * 72], abs=0.1)


@pytest.mark.parametrize("participant", ["Sample Participant", "أحمد صالح"])
def test_both_outputs_support_blank_event_fields(tmp_path, participant):
    paths = ce.edit_certificate(
        "template_certificate_no_line.jpg", [participant], "", "",
        ce.name_font_path, ce.regular_font_path, str(tmp_path), output_format="Both",
    )

    assert {Path(path).suffix for path in paths} == {".jpg", ".pdf"}
    assert all(Path(path).is_file() for path in paths)
    pdf_path = next(path for path in paths if Path(path).suffix == ".pdf")
    box, _ = read_pdf_output(pdf_path)
    assert box[2:] == pytest.approx([297 / 25.4 * 72, 210 / 25.4 * 72], abs=0.1)


@pytest.mark.parametrize("participant", ["Sample Participant", "أحمد صالح"])
def test_preview_remains_original_size_jpg_with_blank_event_fields(tmp_path, participant):
    path = ce.preview_certificate(
        "template_certificate_no_line.jpg", participant, "", "",
        ce.name_font_path, ce.regular_font_path, str(tmp_path),
    )

    assert Path(path).suffix == ".jpg"
    with Image.open(path) as preview, Image.open(ce.resource_path("template_certificate_no_line.jpg")) as source:
        assert preview.size == source.size
    assert list(tmp_path.glob("*.pdf")) == []


@pytest.mark.parametrize("participant", [
    "أحمد محمد", "محمد أحمد Alexander Johnson", "Alex Johnson",
])
@pytest.mark.parametrize("event_title,event_date", [("", ""), ("Workshop", "27/03/2026")])
def test_name_layout_used_by_both_exports_and_preview(
    tmp_path, monkeypatch, participant, event_title, event_date,
):
    # Observe the real renderer, not a replacement for it; verify both bounds
    # and that export/preview use exactly the same layout before JPEG encoding.
    layouts = []
    original = ce._layout_name

    def record(draw, *args, **kwargs):
        lines = original(draw, *args, **kwargs)
        layouts.append([(text, font.size, draw.textbbox(xy, text, font=font))
                        for text, font, xy in lines])
        return lines

    monkeypatch.setattr(ce, "_layout_name", record)
    paths = ce.edit_certificate(
        "template_certificate_no_line.jpg", [participant], event_title, event_date,
        ce.name_font_path, ce.regular_font_path, str(tmp_path), output_format="Both",
    )
    preview = ce.preview_certificate(
        "template_certificate_no_line.jpg", participant, event_title, event_date,
        ce.name_font_path, ce.regular_font_path, str(tmp_path),
    )
    assert layouts[0] == layouts[1]
    assert len(layouts[0]) == (1 if participant == "أحمد محمد" else 2)
    with Image.open(ce.resource_path("template_certificate_no_line.jpg")) as template:
        sx, sy = template.width / ce.REF_W, template.height / ce.REF_H
        expected_size = template.size
    for _, _, (left, top, right, bottom) in layouts[0]:
        assert left >= round(90 * sx)
        assert right <= round(1165 * sx)
        assert top >= round(485 * sy)
        assert bottom <= round(865 * sy)
    jpg_path = next(p for p in paths if Path(p).suffix == ".jpg")
    pdf_path = next(p for p in paths if Path(p).suffix == ".pdf")
    with Image.open(jpg_path) as jpg, Image.open(preview) as image_preview:
        assert jpg.size == expected_size
        assert ImageChops.difference(jpg, image_preview).getbbox() is None
    box, embedded = read_pdf_output(pdf_path)
    assert embedded.size == (3508, 2480)
    assert box == pytest.approx([0, 0, 297 / 25.4 * 72, 210 / 25.4 * 72], abs=0.1)
