from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display

import sys


BASE_DIR = Path(__file__).resolve().parent


def resource_path(relative_path: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return BASE_DIR / relative_path

# Latin/default fonts
name_font_path = str(resource_path("Open_Sans/static/OpenSans-Bold.ttf"))
regular_font_path = str(resource_path("Open_Sans/static/OpenSans-Regular.ttf"))

# Arabic fonts
arabic_name_font_path = r"C:\Windows\Fonts\arialbd.ttf"
arabic_regular_font_path = r"C:\Windows\Fonts\arial.ttf"

REF_W, REF_H = 2048, 1447

NAME_X_R = 115
NAME_Y1_R = 485

EVENT_X_R = 115
EVENT_Y_R = 980

DATE_X_R = 115
DATE_Y_R = 1090

MAX_NAME_WIDTH_R = 1200
MAX_EVENT_WIDTH_R = 1750

NAME_FONT_START_R = 165
NAME_FONT_MIN_R = 95

EVENT_FONT_START_R = 62
EVENT_FONT_MIN_R = 36

DATE_FONT_R = 44

LINE_PADDING_R = 25

def _contains_arabic(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if (
            0x0600 <= code <= 0x06FF
            or 0x0750 <= code <= 0x077F
            or 0x08A0 <= code <= 0x08FF
            or 0xFB50 <= code <= 0xFDFF
            or 0xFE70 <= code <= 0xFEFF
        ):
            return True
    return False


def _shape_arabic_text(text: str) -> str:
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def _split_name_two_lines(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) <= 1:
        return full_name.strip(), ""
    return parts[0], " ".join(parts[1:])


def _pretty_date_ddmmyyyy(date_text: str) -> str:
    dt = datetime.strptime(date_text.strip(), "%d/%m/%Y")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"

def _safe_filename(name: str, index: int | None = None) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in name).strip()
    cleaned = cleaned.rstrip(" .")
    cleaned = " ".join(cleaned.split())

    if not cleaned:
        cleaned = f"certificate_{index}" if index is not None else "certificate"

    return cleaned

def _unique_output_base_path(
    save_dir_path: Path,
    full_name: str,
    index: int,
    suffixes: list[str],
) -> Path:
    base_name = _safe_filename(full_name, index + 1)
    candidate = save_dir_path / base_name

    if not any((candidate.with_suffix(suffix)).exists() for suffix in suffixes):
        return candidate

    counter = 2
    while True:
        candidate = save_dir_path / f"{base_name} ({counter})"
        if not any((candidate.with_suffix(suffix)).exists() for suffix in suffixes):
            return candidate
        counter += 1


def _unique_output_path(save_dir_path: Path, full_name: str, index: int) -> Path:
    base_path = _unique_output_base_path(
        save_dir_path=save_dir_path,
        full_name=full_name,
        index=index,
        suffixes=[".jpg"],
    )
    return base_path.with_suffix(".jpg")


def _save_certificate_outputs(
    template: Image.Image,
    save_dir_path: Path,
    full_name: str,
    index: int,
    output_format: str,
) -> list[str]:
    normalized_format = str(output_format).strip().upper()

    if normalized_format == "JPG":
        suffixes = [".jpg"]
    elif normalized_format == "PDF":
        suffixes = [".pdf"]
    elif normalized_format == "BOTH":
        suffixes = [".jpg", ".pdf"]
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    base_output_path = _unique_output_base_path(
        save_dir_path=save_dir_path,
        full_name=full_name,
        index=index,
        suffixes=suffixes,
    )

    saved_paths: list[str] = []

    if ".jpg" in suffixes:
        jpg_path = base_output_path.with_suffix(".jpg")
        template.save(jpg_path, format="JPEG", quality=95)
        saved_paths.append(str(jpg_path))

    if ".pdf" in suffixes:
        pdf_path = base_output_path.with_suffix(".pdf")
        template.convert("RGB").save(pdf_path, format="PDF", resolution=100.0)
        saved_paths.append(str(pdf_path))

    return saved_paths


def _load_font(font_path: str, size: int):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception as e:
        raise RuntimeError(f"Could not load font '{font_path}': {e}")


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    start: int,
    min_size: int,
    max_width: int,
):
    size = start
    step = max(2, start // 60)

    while size >= min_size:
        font = _load_font(font_path, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= step

    return _load_font(font_path, min_size)


def _draw_name(
    draw: ImageDraw.ImageDraw,
    full_name: str,
    x: int,
    y1: int,
    max_width: int,
    line_padding: int,
    latin_font_path: str,
    arabic_font_path: str,
    font_start: int,
    font_min: int,
):
    if _contains_arabic(full_name):
        first, second = _split_name_two_lines(full_name)

        first_shaped = _shape_arabic_text(first)
        second_shaped = _shape_arabic_text(second) if second else ""

        font1 = _fit_font(
            draw,
            first_shaped,
            arabic_font_path,
            start=font_start,
            min_size=font_min,
            max_width=max_width,
        )
        draw.text((x, y1), first_shaped, fill=(0, 0, 0), font=font1)

        if second_shaped:
            bbox1 = draw.textbbox((x, y1), first_shaped, font=font1)
            line1_h = bbox1[3] - bbox1[1]
            y2 = y1 + line1_h + line_padding

            font2 = _fit_font(
                draw,
                second_shaped,
                arabic_font_path,
                start=font_start,
                min_size=font_min,
                max_width=max_width,
            )
            draw.text((x, y2), second_shaped, fill=(0, 0, 0), font=font2)
    else:
        first, second = _split_name_two_lines(full_name)

        font1 = _fit_font(
            draw,
            first,
            latin_font_path,
            start=font_start,
            min_size=font_min,
            max_width=max_width,
        )
        draw.text((x, y1), first, fill=(0, 0, 0), font=font1)

        if second:
            bbox1 = draw.textbbox((x, y1), first, font=font1)
            line1_h = bbox1[3] - bbox1[1]
            y2 = y1 + line1_h + line_padding

            font2 = _fit_font(
                draw,
                second,
                latin_font_path,
                start=font_start,
                min_size=font_min,
                max_width=max_width,
            )
            draw.text((x, y2), second, fill=(0, 0, 0), font=font2)


def edit_certificate(
    template_path: str,
    attendees_list: List[str],
    eventTitle: str,
    eventDate: str,
    name_font_path: str,
    regular_font_path: str,
    save_dir: str,
    progress_callback=None,
    output_format: str = "JPG",
):
    save_dir_path = Path(save_dir)
    save_dir_path.mkdir(parents=True, exist_ok=True)

    template_full_path = Path(template_path)
    if not template_full_path.is_absolute():
        template_full_path = resource_path(template_path)

    if not template_full_path.exists():
        raise FileNotFoundError(f"Template not found: {template_full_path}")

    if not Path(name_font_path).exists():
        raise FileNotFoundError(f"Latin bold font not found: {name_font_path}")

    if not Path(regular_font_path).exists():
        raise FileNotFoundError(f"Latin regular font not found: {regular_font_path}")

    if not Path(arabic_name_font_path).exists():
        raise FileNotFoundError(f"Arabic bold font not found: {arabic_name_font_path}")

    if not Path(arabic_regular_font_path).exists():
        raise FileNotFoundError(f"Arabic regular font not found: {arabic_regular_font_path}")

    # Validate Arabic font file early
    _load_font(arabic_name_font_path, 40)
    _load_font(arabic_regular_font_path, 40)

    list_of_output_paths = []

    for index, full_name in enumerate(attendees_list):
        print(f"Now processing: {full_name}")
        print(f"Contains Arabic: {_contains_arabic(full_name)}")

        template = Image.open(template_full_path).convert("RGB")
        draw = ImageDraw.Draw(template)

        W, H = template.size
        sx = W / REF_W
        sy = H / REF_H
        s = (sx + sy) / 2

        NAME_X = int(round(NAME_X_R * sx))
        NAME_Y1 = int(round(NAME_Y1_R * sy))

        EVENT_X = int(round(EVENT_X_R * sx))
        EVENT_Y = int(round(EVENT_Y_R * sy))

        DATE_X = int(round(DATE_X_R * sx))
        DATE_Y = int(round(DATE_Y_R * sy))

        MAX_NAME_WIDTH = int(round(MAX_NAME_WIDTH_R * sx))
        MAX_EVENT_WIDTH = int(round(MAX_EVENT_WIDTH_R * sx))

        NAME_FONT_START = int(round(NAME_FONT_START_R * s))
        NAME_FONT_MIN = int(round(NAME_FONT_MIN_R * s))

        EVENT_FONT_START = int(round(EVENT_FONT_START_R * s))
        EVENT_FONT_MIN = int(round(EVENT_FONT_MIN_R * s))

        DATE_FONT_SIZE = int(round(DATE_FONT_R * s))
        LINE_PADDING = int(round(LINE_PADDING_R * s))

        _draw_name(
            draw=draw,
            full_name=full_name,
            x=NAME_X,
            y1=NAME_Y1,
            max_width=MAX_NAME_WIDTH,
            line_padding=LINE_PADDING,
            latin_font_path=name_font_path,
            arabic_font_path=arabic_name_font_path,
            font_start=NAME_FONT_START,
            font_min=NAME_FONT_MIN,
        )

        event_title = (eventTitle or "").strip().upper()
        event_font = _fit_font(
            draw,
            event_title,
            name_font_path,
            start=EVENT_FONT_START,
            min_size=EVENT_FONT_MIN,
            max_width=MAX_EVENT_WIDTH,
        )
        draw.text((EVENT_X, EVENT_Y), event_title, fill=(0, 0, 0), font=event_font)

        pretty_date = _pretty_date_ddmmyyyy(eventDate)
        date_font = _load_font(regular_font_path, DATE_FONT_SIZE)
        draw.text((DATE_X, DATE_Y), pretty_date, fill=(120, 120, 120), font=date_font)

        saved_paths = _save_certificate_outputs(
            template=template,
            save_dir_path=save_dir_path,
            full_name=full_name,
            index=index,
            output_format=output_format,
        )

        list_of_output_paths.extend(saved_paths)
        print(f"Processing Certificate {index + 1}/{len(attendees_list)} - {full_name}")

        if progress_callback is not None:
            progress_callback(index + 1, len(attendees_list), full_name)

    return list_of_output_paths

def preview_certificate(
    template_path: str,
    participant_name: str,
    eventTitle: str,
    eventDate: str,
    name_font_path: str,
    regular_font_path: str,
    save_dir: str,
):
    generated_paths = edit_certificate(
        template_path=template_path,
        attendees_list=[participant_name],
        eventTitle=eventTitle,
        eventDate=eventDate,
        name_font_path=name_font_path,
        regular_font_path=regular_font_path,
        save_dir=save_dir,
        output_format="JPG",
    )
    return generated_paths[0] if generated_paths else None