from pathlib import Path
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