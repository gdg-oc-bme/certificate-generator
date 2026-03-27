from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont


# --- Windows-safe font paths ---
BASE_DIR = Path(__file__).resolve().parent
name_font_path = str(BASE_DIR / "Open_Sans" / "static" / "OpenSans-Bold.ttf")
regular_font_path = str(BASE_DIR / "Open_Sans" / "static" / "OpenSans-Regular.ttf")


# Reference design size (the coordinates below were measured against this)
REF_W, REF_H = 2048, 1447

# Reference coordinates for 2026 layout (on REF_W x REF_H)
NAME_X_R = 115
NAME_Y1_R = 520

EVENT_X_R = 115
EVENT_Y_R = 1015

DATE_X_R = 115
DATE_Y_R = 1125

MAX_NAME_WIDTH_R = 1200
MAX_EVENT_WIDTH_R = 1750

# Reference font sizes (scaled automatically)
NAME_FONT_START_R = 165
NAME_FONT_MIN_R = 95

EVENT_FONT_START_R = 62
EVENT_FONT_MIN_R = 36

DATE_FONT_R = 44

LINE_PADDING_R = 25


def _split_name_two_lines(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) <= 1:
        return full_name.strip(), ""
    return parts[0], " ".join(parts[1:])


def _pretty_date_ddmmyyyy(date_text: str) -> str:
    dt = datetime.strptime(date_text.strip(), "%d/%m/%Y")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in name).strip()
    return cleaned if cleaned else "certificate"


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: str, start: int, min_size: int, max_width: int):
    size = start
    step = max(2, start // 60)  # scales step a bit for large images
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= step
    return ImageFont.truetype(font_path, min_size)


def edit_certificate(
    template_path: str,
    attendees_list: List[str],
    eventTitle: str,
    eventDate: str,
    name_font_path: str,
    regular_font_path: str,
    save_dir: str,
):
    save_dir_path = Path(save_dir)
    save_dir_path.mkdir(parents=True, exist_ok=True)

    list_of_jpgs_paths = []

    for index, full_name in enumerate(attendees_list):
        template = Image.open(template_path).convert("RGB")
        draw = ImageDraw.Draw(template)

        W, H = template.size
        sx = W / REF_W
        sy = H / REF_H
        s = (sx + sy) / 2  # scale factor for fonts/padding

        # Scale coordinates
        NAME_X = int(round(NAME_X_R * sx))
        NAME_Y1 = int(round(NAME_Y1_R * sy))

        EVENT_X = int(round(EVENT_X_R * sx))
        EVENT_Y = int(round(EVENT_Y_R * sy))

        DATE_X = int(round(DATE_X_R * sx))
        DATE_Y = int(round(DATE_Y_R * sy))

        MAX_NAME_WIDTH = int(round(MAX_NAME_WIDTH_R * sx))
        MAX_EVENT_WIDTH = int(round(MAX_EVENT_WIDTH_R * sx))

        # Scale fonts
        NAME_FONT_START = int(round(NAME_FONT_START_R * s))
        NAME_FONT_MIN = int(round(NAME_FONT_MIN_R * s))

        EVENT_FONT_START = int(round(EVENT_FONT_START_R * s))
        EVENT_FONT_MIN = int(round(EVENT_FONT_MIN_R * s))

        DATE_FONT_SIZE = int(round(DATE_FONT_R * s))
        LINE_PADDING = int(round(LINE_PADDING_R * s))

        # ---- Name (two lines) ----
        first, second = _split_name_two_lines(full_name)

        font1 = _fit_font(draw, first, name_font_path, start=NAME_FONT_START, min_size=NAME_FONT_MIN, max_width=MAX_NAME_WIDTH)
        draw.text((NAME_X, NAME_Y1), first, fill=(0, 0, 0), font=font1)

        if second:
            bbox1 = draw.textbbox((NAME_X, NAME_Y1), first, font=font1)
            line1_h = bbox1[3] - bbox1[1]
            name_y2 = NAME_Y1 + line1_h + LINE_PADDING

            font2 = _fit_font(draw, second, name_font_path, start=NAME_FONT_START, min_size=NAME_FONT_MIN, max_width=MAX_NAME_WIDTH)
            draw.text((NAME_X, name_y2), second, fill=(0, 0, 0), font=font2)

        # ---- Event title ----
        event_title = (eventTitle or "").strip().upper()
        event_font = _fit_font(draw, event_title, name_font_path, start=EVENT_FONT_START, min_size=EVENT_FONT_MIN, max_width=MAX_EVENT_WIDTH)
        draw.text((EVENT_X, EVENT_Y), event_title, fill=(0, 0, 0), font=event_font)

        # ---- Date ----
        pretty_date = _pretty_date_ddmmyyyy(eventDate)
        date_font = ImageFont.truetype(regular_font_path, DATE_FONT_SIZE)
        draw.text((DATE_X, DATE_Y), pretty_date, fill=(120, 120, 120), font=date_font)

        out_name = _safe_filename(full_name) + ".jpg"
        output_path = save_dir_path / out_name
        template.save(output_path, quality=95)

        list_of_jpgs_paths.append(str(output_path))
        print(f"Processing Certificate {index + 1}/{len(attendees_list)}")

    return list_of_jpgs_paths