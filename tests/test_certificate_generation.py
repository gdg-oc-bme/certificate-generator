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
