from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

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

MAX_NAME_WIDTH_R = 1050
# Stop above the template's "For successfully participating" caption.
NAME_AREA_BOTTOM_R = 865
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
    date_text = (date_text or "").strip()

    if not date_text:
        return ""

    dt = datetime.strptime(date_text, "%d/%m/%Y")
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
        # A4 landscape at 300 DPI, fitted without cropping or stretching.
        pdf_image = ImageOps.pad(
            template.convert("RGB"),
            (3508, 2480),
            method=Image.Resampling.LANCZOS,
            color="white",
            centering=(0.5, 0.5),
        )
        pdf_image.save(
            pdf_path, format="PDF", resolution=300.0,
            quality=90, optimize=True, subsampling=0,
        )
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


def _layout_name(
    draw, full_name, x, y1, max_width, max_height, line_padding,
    latin_font_path, arabic_font_path, font_start, font_min,
    *, keep_script_runs=True, _cache=None,
):
    """Return (visual text, font, origin) lines, checking all ink before drawing.

    Preserve the established Latin two-line layout when it fits. Otherwise wrap
    logical words within script runs, then shape each completed Arabic line once.
    Pre-shaped Arabic uses BASIC so a RAQM-enabled wheel cannot reorder it again.
    """
    words = full_name.split()
    if not words:
        raise ValueError("Certificate name cannot fit: no visible name supplied.")

    def fits(lines):
        previous_bottom = None
        for text, font, origin in lines:
            left, top, right, bottom = draw.textbbox(origin, text, font=font)
            # The original Latin origin permits a small italic/glyph overhang.
            if (left < x - line_padding or right > x + max_width
                    or top < y1 or bottom > y1 + max_height
                    or right <= left or bottom <= top
                    or (previous_bottom is not None
                        and top < previous_bottom + line_padding)):
                return False
            previous_bottom = bottom
        return True

    if not _contains_arabic(full_name):
        first, second = _split_name_two_lines(full_name)
        legacy = []
        y = y1
        for text in (first, second):
            if not text:
                continue
            font = _fit_font(draw, text, latin_font_path, font_start, font_min, max_width)
            legacy.append((text, font, (x, y)))
            box = draw.textbbox((x, y), text, font=font)
            y += box[3] - box[1] + line_padding
        if fits(legacy):
            return legacy

    # Keep adjacent Arabic/Latin words together, rather than applying bidi to
    # independently placed Latin text or reversing the order of script blocks.
    runs = []
    for word in words:
        arabic = _contains_arabic(word)
        if not runs or runs[-1][0] != arabic:
            runs.append((arabic, [word]))
        else:
            runs[-1][1].append(word)
    if not keep_script_runs:
        # If many alternating runs would consume too many rows, lay out whole
        # logical lines with one Unicode font and one bidi pass per mixed line.
        runs = [(True, words)]

    # Caches live only for this layout (including its mixed-line fallback).
    fonts, shaped, measurements, layouts = _cache if _cache is not None else ({}, {}, {}, {})

    def wrapped_lines(size):
        key = (keep_script_runs, size)
        if key in layouts:
            return layouts[key]
        wrapped = []
        for arabic, run_words in runs:
            path = arabic_font_path if arabic else latin_font_path
            font_key = (path, size)
            if font_key not in fonts:
                fonts[font_key] = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
            font = fonts[font_key]

            def measured(logical):
                shape_key = (arabic, logical)
                if shape_key not in shaped:
                    shaped[shape_key] = _shape_arabic_text(logical) if arabic else logical
                measure_key = (font_key, shape_key)
                if measure_key not in measurements:
                    visual = shaped[shape_key]
                    measurements[measure_key] = (visual, draw.textbbox((0, 0), visual, font=font))
                return measurements[measure_key]

            line = ""
            for word in run_words:
                candidate = (line + " " + word).strip()
                _, box = measured(candidate)
                if box[2] - box[0] > max_width:
                    if line:
                        wrapped.append((*measured(line), font))
                    line = word
                else:
                    line = candidate
            if line:
                wrapped.append((*measured(line), font))

        lines = []
        top = y1 + round(font_start / 4)
        for visual, box, font in wrapped:
            lines.append((visual, font, (x - box[0], top - box[1])))
            top += box[3] - box[1] + line_padding
        layouts[key] = lines
        return lines

    largest = wrapped_lines(font_start)
    if len(largest) == len(runs) and fits(largest):
        return largest

    # Wrapping at the readable floor establishes the attainable row count.
    # Locate its largest font first, rather than scanning every point size.
    row_floor = len(wrapped_lines(font_min))
    low, high = font_min, font_start
    while low < high:
        middle = (low + high + 1) // 2
        if len(wrapped_lines(middle)) <= row_floor:
            low = middle
        else:
            high = middle - 1
    best = None
    for size in range(low, font_min - 1, -1):
        lines = wrapped_lines(size)
        if len(lines) == row_floor and fits(lines):
            best = lines
            break
    if best is None:
        # Height constraints can make the lowest row count infeasible. Retain
        # the original bounded search and preference for fewer rows in that case.
        for size in range(font_start, font_min - 1, -1):
            lines = wrapped_lines(size)
            if fits(lines) and (best is None or len(lines) < len(best)):
                best = lines
    if best is None:
        if keep_script_runs and len(runs) > 1:
            return _layout_name(
                draw, full_name, x, y1, max_width, max_height, line_padding,
                latin_font_path, arabic_font_path, font_start, font_min,
                keep_script_runs=False, _cache=(fonts, shaped, measurements, layouts),
            )
        raise ValueError("Certificate name cannot fit within the available name area.")
    return best


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
    max_height: int,
):
    lines = _layout_name(
        draw, full_name, x, y1, max_width, max_height, line_padding,
        latin_font_path, arabic_font_path, font_start, font_min,
    )
    for text, font, origin in lines:
        draw.text(origin, text, fill=(0, 0, 0), font=font)


class CertificateLayoutError(ValueError):
    def __init__(self, message, *, participant=None, participant_index=None, csv_row=None):
        super().__init__(message)
        self.participant = participant
        self.participant_index = participant_index
        self.csv_row = csv_row


class CertificateGenerationError(RuntimeError):
    def __init__(self, participant, completed_count, completed_paths, cause):
        self.completed_count = completed_count
        self.completed_paths = tuple(completed_paths)
        outputs = "\n".join(completed_paths) or "None"
        super().__init__(
            f'Generation failed for "{participant}": {cause}\n'
            f'Completed participants: {completed_count}. Known completed outputs:\n{outputs}\n'
            'Additional partial files may exist. Review the output folder before retrying.'
        )


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
    participant_rows: list[int] | None = None,
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

    with Image.open(template_full_path) as source:
        original_template = source.convert("RGB")
    template = original_template
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

    layout_args = dict(
        x=NAME_X, y1=NAME_Y1, max_width=MAX_NAME_WIDTH,
        max_height=int(round(NAME_AREA_BOTTOM_R * sy)) - NAME_Y1,
        line_padding=LINE_PADDING, latin_font_path=name_font_path,
        arabic_font_path=arabic_name_font_path,
        font_start=NAME_FONT_START, font_min=NAME_FONT_MIN,
    )
    measuring_draw = ImageDraw.Draw(original_template)
    prepared = {}
    # Validate every eligible name before the first output is saved. Layouts
    # (and their font objects) are reused for identical names within this batch.
    for index, full_name in enumerate(attendees_list):
        if full_name in prepared:
            continue
        try:
            prepared[full_name] = _layout_name(measuring_draw, full_name, **layout_args)
        except ValueError as error:
            row = participant_rows[index] if participant_rows is not None else None
            location = f"participant {index + 1}"
            if row is not None:
                location += f", CSV row {row}"
            raise CertificateLayoutError(
                f'Certificate name cannot fit for {location}: "{full_name}". '
                'No certificates were saved for this batch; generation did not begin. '
                'Correct or shorten this name and retry.',
                participant=full_name, participant_index=index + 1, csv_row=row,
            ) from error

    list_of_output_paths = []
    completed_count = 0
    for index, full_name in enumerate(attendees_list):
        try:
            template = original_template.copy()
            draw = ImageDraw.Draw(template)
            for text, font, origin in prepared[full_name]:
                draw.text(origin, text, fill=(0, 0, 0), font=font)

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
            completed_count += 1

            if progress_callback is not None:
                progress_callback(index + 1, len(attendees_list), full_name)

        except Exception as error:
            raise CertificateGenerationError(full_name, completed_count, list_of_output_paths, error) from error

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
