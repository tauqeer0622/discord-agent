import os
import json
import random
import textwrap
from datetime import datetime
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
LAYOUTS_FILE = os.path.join(TEMPLATES_DIR, "layouts.json")

def load_template_layouts() -> Dict[str, Any]:
    """Loads custom template layout mappings from layouts.json."""
    if os.path.exists(LAYOUTS_FILE):
        try:
            with open(LAYOUTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_template_layouts(layouts: Dict[str, Any]):
    """Saves custom template layout configurations to layouts.json."""
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    with open(LAYOUTS_FILE, "w", encoding="utf-8") as f:
        json.dump(layouts, f, indent=2)

def get_available_templates() -> List[str]:
    """
    Dynamically scans the templates/ directory for all supported image files.
    Allows seamlessly adding new templates at any time just by dropping files into the folder.
    """
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    if not os.path.exists(TEMPLATES_DIR):
        return []

    found = []
    for fname in sorted(os.listdir(TEMPLATES_DIR)):
        if fname.startswith("test_") or fname.startswith(".") or fname.startswith("_"):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in valid_exts:
            full_path = os.path.join(TEMPLATES_DIR, fname)
            if os.path.isfile(full_path):
                found.append(fname)

    return found or [
        "template_1_three_phones.jpg",
        "template_2_paper_notepad.jpg",
        "template_3_globe_single_phone.jpg",
        "template_4_calendar_chart.jpg",
        "template_5_split_events_phone.jpg"
    ]

# Backward-compatibility alias
AVAILABLE_TEMPLATES = get_available_templates()

def get_event_date_object(data: Dict[str, Any]) -> datetime:
    """Extracts date object from data or defaults to current UTC time."""
    raw_d = data.get("date")
    if raw_d:
        try:
            return datetime.strptime(str(raw_d)[:10], "%Y-%m-%d")
        except Exception:
            pass
    return datetime.utcnow()

def get_font(size: int, bold: bool = False):
    """Loads TrueType font with cross-platform fallback (bundled fonts, Windows, Linux, Pillow default)."""
    bundled_names = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    for name in bundled_names:
        p = os.path.join(TEMPLATES_DIR, "fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass

    windows_names = (
        ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    )
    for name in windows_names:
        path = os.path.join("C:/Windows/Fonts", name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass

    linux_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for lp in linux_paths:
        if os.path.exists(lp):
            try:
                return ImageFont.truetype(lp, size)
            except Exception:
                pass

    for name in bundled_names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass

    return ImageFont.load_default()

def draw_flag(img: Image.Image, x: int, y: int, country_code: str):
    """Draws a clean, crisp 32x20 mini flag or currency badge."""
    w, h = 32, 20
    draw = ImageDraw.Draw(img)
    cc = (country_code or "USD").upper()

    if cc in ("US", "USD"):
        draw.rectangle([x, y, x + w, y + h], fill=(178, 34, 52), outline=(30, 30, 30))
        for s in range(1, 6, 2):
            draw.rectangle([x, y + s * 3, x + w, y + s * 3 + 2], fill=(255, 255, 255))
        draw.rectangle([x, y, x + 15, y + 11], fill=(60, 59, 110))
    elif cc in ("EU", "EUR"):
        draw.rectangle([x, y, x + w, y + h], fill=(0, 51, 153), outline=(30, 30, 30))
        draw.ellipse([x + 12, y + 6, x + 20, y + 14], outline=(255, 204, 0), width=2)
    elif cc in ("JP", "JPY"):
        draw.rectangle([x, y, x + w, y + h], fill=(255, 255, 255), outline=(180, 180, 180))
        draw.ellipse([x + 10, y + 4, x + 22, y + 16], fill=(188, 0, 45))
    elif cc in ("GB", "GBP", "UK"):
        draw.rectangle([x, y, x + w, y + h], fill=(1, 33, 105), outline=(30, 30, 30))
        draw.rectangle([x + 13, y, x + 19, y + h], fill=(255, 255, 255))
        draw.rectangle([x, y + 7, x + w, y + 13], fill=(255, 255, 255))
        draw.rectangle([x + 14, y, x + 18, y + h], fill=(200, 16, 46))
        draw.rectangle([x, y + 8, x + w, y + 12], fill=(200, 16, 46))
    elif cc in ("CA", "CAD"):
        draw.rectangle([x, y, x + w, y + h], fill=(216, 6, 33), outline=(30, 30, 30))
        draw.rectangle([x + 8, y, x + 24, y + h], fill=(255, 255, 255))
        draw.polygon([(x + 16, y + 4), (x + 13, y + 14), (x + 19, y + 14)], fill=(216, 6, 33))
    elif cc in ("CN", "CNY"):
        draw.rectangle([x, y, x + w, y + h], fill=(222, 41, 16), outline=(30, 30, 30))
        draw.polygon([(x + 6, y + 5), (x + 8, y + 10), (x + 4, y + 10)], fill=(255, 222, 0))
    elif cc in ("AU", "AUD"):
        draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 139), outline=(30, 30, 30))
        draw.rectangle([x, y, x + 16, y + 10], fill=(1, 33, 105))
        draw.rectangle([x + 6, y, x + 10, y + 10], fill=(255, 255, 255))
        draw.rectangle([x, y + 3, x + 16, y + 7], fill=(255, 255, 255))
        draw.rectangle([x + 7, y, x + 9, y + 10], fill=(200, 16, 46))
        draw.rectangle([x, y + 4, x + 16, y + 6], fill=(200, 16, 46))
        draw.ellipse([x + 22, y + 4, x + 25, y + 7], fill=(255, 255, 255))
        draw.ellipse([x + 24, y + 12, x + 27, y + 15], fill=(255, 255, 255))
    elif cc in ("CH", "CHF"):
        draw.rectangle([x, y, x + w, y + h], fill=(218, 41, 28), outline=(30, 30, 30))
        draw.rectangle([x + 13, y + 4, x + 19, y + 16], fill=(255, 255, 255))
        draw.rectangle([x + 7, y + 7, x + 25, y + 13], fill=(255, 255, 255))
    else:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=(15, 23, 42), outline=(51, 65, 85))
        draw.text((x + 4, y + 4), cc[:3], font=get_font(10, bold=True), fill=(255, 255, 255))

def draw_impact_badge(draw: ImageDraw.Draw, x: int, y: int, impact: str, is_paper: bool = False):
    """Draws color-coded High / Medium / Low impact pill."""
    imp = (impact or "Medium").capitalize()
    w, h = 64, 22
    if "High" in imp:
        bg = (239, 68, 68) if is_paper else (185, 28, 28)
        border = (252, 165, 165) if is_paper else (239, 68, 68)
        text_col = (255, 255, 255)
    elif "Med" in imp:
        bg = (234, 179, 8) if is_paper else (161, 98, 7)
        border = (253, 224, 71) if is_paper else (234, 179, 8)
        text_col = (255, 255, 255) if not is_paper else (0, 0, 0)
    else:
        bg = (100, 116, 139)
        border = (148, 163, 184)
        text_col = (255, 255, 255)

    draw.rounded_rectangle([x, y, x + w, y + h], radius=11, fill=bg, outline=border, width=1)
    tx = x + 16 if "High" in imp else x + 10
    draw.text((tx, y + 3), imp, font=get_font(12, bold=True), fill=text_col)

# ── TEMPLATE SPECIFIC OVERLAY RENDERERS ─────────────────────────

def render_template_1(img: Image.Image, data: Dict[str, Any]):
    """Template 1: 3 Phones with Bottom 2-Column Key Events Table."""
    draw = ImageDraw.Draw(img)
    f_date = get_font(11, bold=True)
    f_time = get_font(11, bold=True)
    f_curr = get_font(11, bold=True)
    f_title = get_font(11, bold=False)

    # 1. Date Pill
    # The pristine pill is at X: 470..703, Y: 775..817 with glowing red border and red calendar icon at 485..515.
    # We clear the text interior [520, 782, 698, 808] with the matching dark red background (45, 2, 6):
    draw.rectangle([520, 782, 698, 808], fill=(45, 2, 6))
    date_str = data.get("formatted_date", datetime.utcnow().strftime("%a, %b %d, %Y (UTC)")).upper()
    draw.text((524, 786), date_str, font=f_date, fill=(255, 255, 255))

    # 2. Events Table (Clear rows strictly below headers from X: 30 to 788, Y: 835 to 955, preserving headers at 820..833)
    events = data.get("events", [])
    draw.rectangle([30, 835, 788, 955], fill=(2, 14, 24))

    num_events = len(events)
    split_threshold = (num_events + 1) // 2 if num_events <= 6 else 5
    row_h = 36
    for i, ev in enumerate(events[:10]):
        col_right = i >= split_threshold
        col_i = i if not col_right else i - split_threshold
        base_x = 35 if not col_right else 425
        cy = 838 + col_i * row_h

        # Pin dot
        imp = ev.get("impact", "Medium")
        pin_col = (239, 68, 68) if "High" in imp else (234, 179, 8)
        draw.ellipse([base_x + 5, cy + 6, base_x + 13, cy + 14], fill=pin_col)

        # Time
        draw.text((base_x + 20, cy + 4), ev.get("time", "12:00"), font=f_time, fill=(255, 255, 255))

        # Flag
        draw_flag(img, base_x + 65, cy + 3, ev.get("country", ev.get("currency", "USD")))

        # Currency
        draw.text((base_x + 104, cy + 4), ev.get("currency", "USD"), font=f_curr, fill=(203, 213, 225))

        # Title (truncate to 17 chars to prevent touching badges)
        t_text = ev.get("event", "Economic Event")
        if len(t_text) > 17:
            t_text = t_text[:16] + ".."
        draw.text((base_x + 140, cy + 4), t_text, font=f_title, fill=(255, 255, 255))

        # Impact pill
        draw_impact_badge(draw, base_x + 285, cy + 1, imp)

def render_template_2(img: Image.Image, data: Dict[str, Any]):
    """Template 2: Torn Paper Notepad Table."""
    draw = ImageDraw.Draw(img)
    f_cal_date = get_font(23, bold=True)
    f_cal_year = get_font(18, bold=True)

    f_time = get_font(12, bold=True)
    f_curr = get_font(12, bold=True)
    f_title = get_font(12, bold=True)
    f_reason = get_font(10, bold=False)

    # 1. Calendar Pad Date (Inside spiral desk calendar)
    now = get_event_date_object(data)
    m_day = now.strftime("%b %d").upper()
    year_str = now.strftime("%Y")
    
    # Clear old date text cleanly inside paper bounds [498, 148, 622, 222] with paper color (246, 246, 246)
    pad_poly = [(498, 148), (612, 148), (622, 222), (504, 222)]
    draw.polygon(pad_poly, fill=(246, 246, 246))

    cx = 558
    w_d = f_cal_date.getbbox(m_day)[2]
    draw.text((cx - w_d // 2, 160), m_day, font=f_cal_date, fill=(20, 20, 20))
    w_y = f_cal_year.getbbox(year_str)[2]
    draw.text((cx - w_y // 2, 187), year_str, font=f_cal_year, fill=(20, 20, 20))

    # Hand-drawn orange-red highlighter circle around the date, exactly as shared!
    circle_path = os.path.join(TEMPLATES_DIR, "hand_drawn_circle.png")
    if os.path.exists(circle_path):
        circle_img = Image.open(circle_path)
        img.paste(circle_img, (500, 140), circle_img)
    else:
        draw.ellipse([508, 146, 616, 220], outline=(235, 85, 30), width=3)
        draw.arc([506, 144, 618, 222], start=15, end=335, fill=(245, 105, 35), width=2)

    # 2. Events on Ruled Paper (X: 50 to 770, Y: 300 to 632 - preserving blue headers at 270..296 and clearing row 8)
    events = data.get("events", [])
    paper_bg = (248, 244, 236)
    draw.rectangle([50, 300, 770, 632], fill=paper_bg)

    row_h = 58
    for i, ev in enumerate(events[:5]):
        cy = 305 + i * row_h
        # Draw light ruling line
        draw.line([(50, cy + row_h - 2), (770, cy + row_h - 2)], fill=(225, 220, 210), width=1)

        # Time
        draw.text((58, cy + 8), ev.get("time", "12:00"), font=f_time, fill=(30, 41, 59))

        # Flag
        draw_flag(img, 120, cy + 8, ev.get("country", ev.get("currency", "USD")))

        # Currency
        draw.text((160, cy + 9), ev.get("currency", "USD"), font=f_curr, fill=(71, 85, 105))

        # Event Title
        ev_title = ev.get("event", "Key Economic Event")
        if len(ev_title) > 34:
            ev_title = ev_title[:33] + ".."
        draw.text((210, cy + 3), ev_title, font=f_title, fill=(30, 41, 59))

        # Reason subtitle
        reason = ev.get("reason", "A key indicator for market direction.")
        if len(reason) > 52:
            reason = reason[:51] + ".."
        draw.text((210, cy + 24), f"Critical Reason: {reason}", font=f_reason, fill=(180, 20, 20))

        # Impact Badge
        draw_impact_badge(draw, 690, cy + 10, ev.get("impact", "Medium"), is_paper=True)

def render_template_3(img: Image.Image, data: Dict[str, Any]):
    """Template 3: Glowing Blue Key Events Box with 3D Globe."""
    draw = ImageDraw.Draw(img)
    f_date = get_font(11, bold=True)
    f_time = get_font(12, bold=True)
    f_curr = get_font(12, bold=True)
    f_title = get_font(13, bold=True)
    f_reason = get_font(10, bold=False)

    # 1. Date Pill (X: 568 to 792, Y: 523 to 558)
    draw.rectangle([568, 523, 792, 558], fill=(5, 16, 28))
    draw.rounded_rectangle([568, 523, 792, 558], radius=6, fill=(5, 16, 28), outline=(14, 165, 233), width=1)

    # Calendar icon
    draw.rectangle([578, 532, 594, 550], outline=(14, 165, 233), fill=(10, 25, 45))
    draw.rectangle([578, 532, 594, 537], fill=(14, 165, 233))
    for dot_x in (582, 586, 590):
        for dot_y in (540, 545):
            draw.point((dot_x, dot_y), fill=(14, 165, 233))

    date_str = data.get("formatted_date", get_event_date_object(data).strftime("%a, %b %d, %Y (UTC)")).upper()
    draw.text((604, 534), date_str[:22], font=f_date, fill=(255, 255, 255))

    # 2. Events Area (X: 32 to 785, Y: 565 to 830)
    events = data.get("events", [])
    table_bg = (4, 17, 25)
    draw.rectangle([32, 565, 785, 830], fill=table_bg)

    row_h = 52
    for i, ev in enumerate(events[:5]):
        cy = 568 + i * row_h
        # Divider line
        if i > 0:
            draw.line([(32, cy - 2), (785, cy - 2)], fill=(12, 35, 50), width=1)

        imp = ev.get("impact", "High")
        pin_col = (239, 68, 68) if "High" in imp else (234, 179, 8)

        # Timeline vertical pin
        draw.ellipse([38, cy + 6, 52, cy + 20], fill=pin_col)
        draw.rectangle([43, cy + 20, 47, cy + 42], fill=pin_col)

        # Time
        draw.text((58, cy + 4), ev.get("time", "12:00"), font=f_time, fill=(255, 255, 255))
        draw.text((58, cy + 20), "UTC", font=get_font(10, bold=False), fill=(148, 163, 184))

        # Flag
        draw_flag(img, 115, cy + 8, ev.get("country", ev.get("currency", "USD")))

        # Currency
        draw.text((158, cy + 10), ev.get("currency", "USD"), font=f_curr, fill=(203, 213, 225))

        # Event Title
        ev_title = ev.get("event", "Economic Event")
        if len(ev_title) > 38:
            ev_title = ev_title[:37] + ".."
        draw.text((215, cy + 3), ev_title, font=f_title, fill=(255, 255, 255))

        # Subtitle
        reason = ev.get("reason", "A key indicator for market direction.")
        if len(reason) > 55:
            reason = reason[:54] + ".."
        draw.text((215, cy + 22), reason, font=f_reason, fill=(148, 163, 184))

        # Impact Badge
        draw_impact_badge(draw, 685, cy + 10, imp)
        draw.text((758, cy + 12), ">", font=f_curr, fill=(148, 163, 184))

def render_template_4(img: Image.Image, data: Dict[str, Any]):
    """Template 4: Calendar Chart Header with 5 Key Events Rows."""
    draw = ImageDraw.Draw(img)
    f_date = get_font(10, bold=True)
    f_time = get_font(11, bold=True)
    f_curr = get_font(11, bold=True)
    f_title = get_font(12, bold=True)
    f_reason = get_font(9, bold=False)

    # 1. Notepad Calendar top-right
    now = get_event_date_object(data)
    m_day = now.strftime("%b %d").upper()
    year_str = now.strftime("%Y")
    f_cal_mday = get_font(19, bold=True)
    f_cal_yr = get_font(15, bold=True)

    # Feathered mask for paper patch to avoid sharp borders
    poly = [(614, 176), (752, 179), (746, 230), (608, 228)]
    mask = Image.new("L", img.size, 0)
    d_mask = ImageDraw.Draw(mask)
    d_mask.polygon(poly, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.0))

    patch = Image.new("RGB", img.size, (250, 252, 255))
    img.paste(patch, (0, 0), mask)

    cx = 684
    w_mday = f_cal_mday.getbbox(m_day)[2]
    draw.text((cx - w_mday // 2, 183), m_day, font=f_cal_mday, fill=(15, 23, 42))
    w_yr = f_cal_yr.getbbox(year_str)[2]
    draw.text((cx - w_yr // 2, 206), year_str, font=f_cal_yr, fill=(15, 23, 42))

    # Hand-drawn highlighter circle scaled around the date with ample clearance (no overlapping text)
    circle_path = os.path.join(TEMPLATES_DIR, "hand_drawn_circle.png")
    if os.path.exists(circle_path):
        circle_img = Image.open(circle_path)
        c_scaled = circle_img.resize((104, 68), Image.Resampling.LANCZOS)
        img.paste(c_scaled, (cx - c_scaled.width // 2, 168), c_scaled)

    # 2. Date Text in Header (X: 645 to 785, Y: 246 to 260)
    draw.rectangle([645, 246, 785, 260], fill=(2, 14, 25))
    date_str = data.get("formatted_date", datetime.utcnow().strftime("%a, %b %d, %Y (UTC)")).upper()
    w_d = f_date.getbbox(date_str)[2]
    draw.text((782 - w_d, 246), date_str, font=f_date, fill=(255, 255, 255))

    # 3. Events Area (X: 30 to 785, Y: 260 to 484) - completely wipes old row 5 & ghost text without touching app link at 492
    events = data.get("events", [])
    table_bg = (3, 14, 24)
    draw.rectangle([30, 260, 785, 484], fill=table_bg)

    row_h = 42
    for i, ev in enumerate(events[:5]):
        cy = 265 + i * row_h
        if i > 0:
            draw.line([(32, cy - 2), (785, cy - 2)], fill=(12, 35, 50), width=1)

        imp = ev.get("impact", "High")
        pin_col = (239, 68, 68) if "High" in imp else (234, 179, 8)

        draw.ellipse([38, cy + 6, 52, cy + 20], fill=pin_col)
        draw.rectangle([43, cy + 20, 47, cy + 36], fill=pin_col)

        draw.text((58, cy + 3), ev.get("time", "12:00"), font=f_time, fill=(255, 255, 255))
        draw.text((58, cy + 19), "UTC", font=get_font(9, bold=False), fill=(148, 163, 184))

        draw_flag(img, 115, cy + 7, ev.get("country", ev.get("currency", "USD")))
        draw.text((158, cy + 9), ev.get("currency", "USD"), font=f_curr, fill=(203, 213, 225))

        ev_title = ev.get("event", "Economic Event")
        if len(ev_title) > 36:
            ev_title = ev_title[:35] + ".."
        draw.text((215, cy + 2), ev_title, font=f_title, fill=(255, 255, 255))

        reason = ev.get("reason", "Key indicator for market direction.")
        if len(reason) > 52:
            reason = reason[:51] + ".."
        draw.text((215, cy + 20), reason, font=f_reason, fill=(148, 163, 184))

        draw_impact_badge(draw, 685, cy + 7, imp)
        draw.text((758, cy + 9), ">", font=f_curr, fill=(148, 163, 184))

def render_template_5(img: Image.Image, data: Dict[str, Any]):
    """Template 5: Left Half Key Events Card with Right Large Phone."""
    draw = ImageDraw.Draw(img)
    f_date = get_font(10, bold=True)
    f_time = get_font(12, bold=True)
    f_curr = get_font(12, bold=True)
    f_title = get_font(12, bold=True)
    f_reason = get_font(10, bold=False)

    # 1. Date Pill - preserve < and > chevrons and calendar icon
    # Clear text inside [338, 301, 508, 327] with pill dark interior
    draw.rectangle([338, 301, 508, 327], fill=(4, 16, 28))
    date_str = data.get("formatted_date", datetime.utcnow().strftime("%a, %b %d, %Y (UTC)")).upper()
    draw.text((342, 307), date_str, font=f_date, fill=(255, 255, 255))

    # 2. Events Area on Left (X: 25 to 570, Y: 335 to 768) - wipes old row 5 and yellow pin completely, safely above app link at 780
    events = data.get("events", [])
    table_bg = (3, 14, 24)
    draw.rectangle([25, 335, 570, 768], fill=table_bg)

    row_h = 82
    for i, ev in enumerate(events[:5]):
        cy = 340 + i * row_h
        if i > 0:
            draw.line([(25, cy - 4), (570, cy - 4)], fill=(12, 35, 50), width=1)

        imp = ev.get("impact", "High")
        pin_col = (239, 68, 68) if "High" in imp else (234, 179, 8)

        draw.ellipse([34, cy + 8, 48, cy + 22], fill=pin_col)
        draw.rectangle([39, cy + 22, 43, cy + 52], fill=pin_col)

        draw.text((52, cy + 8), ev.get("time", "12:00"), font=f_time, fill=(255, 255, 255))
        draw.text((52, cy + 28), "UTC", font=get_font(9, bold=False), fill=(148, 163, 184))

        draw_flag(img, 96, cy + 12, ev.get("country", ev.get("currency", "USD")))
        draw.text((136, cy + 14), ev.get("currency", "USD"), font=f_curr, fill=(203, 213, 225))

        ev_title = ev.get("event", "Economic Event")
        if len(ev_title) > 26:
            ev_title = ev_title[:25] + ".."
        draw.text((180, cy + 6), ev_title, font=f_title, fill=(255, 255, 255))

        reason = ev.get("reason", "Key indicator for market direction.")
        if len(reason) > 38:
            reason = reason[:37] + ".."
        draw.text((180, cy + 28), reason, font=f_reason, fill=(148, 163, 184))

        draw_impact_badge(draw, 475, cy + 14, imp)
        draw.text((546, cy + 16), ">", font=f_curr, fill=(148, 163, 184))

# ── PARAMETERIZED / CUSTOMIZABLE CARD RENDERER ─────────────────

def render_parameterized_card(img: Image.Image, data: Dict[str, Any], cfg: Dict[str, Any]):
    """
    Renders key events table using customizable coordinates from layouts.json.
    Enables adding any new template design simply by specifying its bounding boxes.
    """
    draw = ImageDraw.Draw(img)
    f_date = get_font(11, bold=True)
    f_time = get_font(12, bold=True)
    f_curr = get_font(12, bold=True)
    f_title = get_font(13, bold=True)
    f_reason = get_font(10, bold=False)

    # 1. Date badge
    date_cfg = cfg.get("date_badge")
    if date_cfg:
        dx1 = date_cfg.get("x1", 572)
        dy1 = date_cfg.get("y1", 512)
        dx2 = date_cfg.get("x2", 790)
        dy2 = date_cfg.get("y2", 552)
        style = date_cfg.get("style", "cyan_outline")
        outline_col = (14, 165, 233) if "cyan" in style else ((220, 38, 38) if "red" in style else (15, 23, 42))
        fill_col = (20, 10, 14) if "red" in style else (5, 16, 28)
        radius = 17 if "pill" in style else 6
        draw.rounded_rectangle([dx1, dy1, dx2, dy2], radius=radius, fill=fill_col, outline=outline_col, width=1)
        date_str = data.get("formatted_date", datetime.utcnow().strftime("%a, %b %d, %Y (UTC)")).upper()
        draw.text((dx1 + 25, dy1 + 8), date_str, font=f_date, fill=(255, 255, 255))

    # 2. Table Area
    tbl = cfg.get("table", {"x1": 32, "y1": 565, "x2": 785, "y2": 830})
    tx1, ty1, tx2, ty2 = tbl.get("x1", 32), tbl.get("y1", 565), tbl.get("x2", 785), tbl.get("y2", 830)
    row_h = cfg.get("row_height", 52)
    max_ev = cfg.get("max_events", 5)

    draw.rectangle([tx1, ty1, tx2, ty2], fill=(4, 17, 25))
    events = data.get("events", [])
    for i, ev in enumerate(events[:max_ev]):
        cy = ty1 + 3 + i * row_h
        if i > 0:
            draw.line([(tx1, cy - 2), (tx2, cy - 2)], fill=(12, 35, 50), width=1)

        imp = ev.get("impact", "High")
        pin_col = (239, 68, 68) if "High" in imp else (234, 179, 8)
        draw.ellipse([tx1 + 6, cy + 6, tx1 + 20, cy + 20], fill=pin_col)
        draw.rectangle([tx1 + 11, cy + 20, tx1 + 15, cy + 42], fill=pin_col)

        draw.text((tx1 + 26, cy + 4), ev.get("time", "12:00"), font=f_time, fill=(255, 255, 255))
        draw.text((tx1 + 26, cy + 20), "UTC", font=get_font(10, bold=False), fill=(148, 163, 184))

        draw_flag(img, tx1 + 83, cy + 8, ev.get("country", ev.get("currency", "USD")))
        draw.text((tx1 + 126, cy + 10), ev.get("currency", "USD"), font=f_curr, fill=(203, 213, 225))

        ev_title = ev.get("event", "Economic Event")
        if len(ev_title) > 38:
            ev_title = ev_title[:37] + ".."
        draw.text((tx1 + 183, cy + 3), ev_title, font=f_title, fill=(255, 255, 255))

        reason = ev.get("reason", "A key indicator for market direction.")
        if len(reason) > 55:
            reason = reason[:54] + ".."
        draw.text((tx1 + 183, cy + 22), reason, font=f_reason, fill=(148, 163, 184))

        draw_impact_badge(draw, tx2 - 100, cy + 10, imp)
        draw.text((tx2 - 27, cy + 12), ">", font=f_curr, fill=(148, 163, 184))

# ── MAIN COMPOSITOR DISPATCHER ──────────────────────────────────

RENDERERS = {
    "template_1_three_phones.jpg": render_template_1,
    "template_2_paper_notepad.jpg": render_template_2,
    "template_3_globe_single_phone.jpg": render_template_3,
    "template_4_calendar_chart.jpg": render_template_4,
    "template_5_split_events_phone.jpg": render_template_5
}

ARCHETYPE_RENDERERS = {
    "bottom_2col_grid": render_template_1,
    "paper_notepad": render_template_2,
    "dark_card_5row": render_template_3,
    "dark_card_chart": render_template_4,
    "split_events_phone": render_template_5
}

def resolve_renderer_for_template(template_name: str):
    """
    Dynamically resolves or creates a renderer for any template (existing or newly added).
    """
    if template_name in RENDERERS:
        return RENDERERS[template_name]

    layouts = load_template_layouts()
    cfg = layouts.get(template_name, {})

    # Check companion json file (e.g. template_6.json alongside template_6.jpg)
    stem = os.path.splitext(template_name)[0]
    companion = os.path.join(TEMPLATES_DIR, f"{stem}.json")
    if os.path.exists(companion):
        try:
            with open(companion, "r", encoding="utf-8") as cf:
                cfg.update(json.load(cf))
        except Exception:
            pass

    # If custom table coordinates are specified, use parameterized renderer
    if "table" in cfg and "archetype" not in cfg:
        return lambda img, data: render_parameterized_card(img, data, cfg)

    archetype = cfg.get("archetype", "").lower()
    if not archetype:
        fname = template_name.lower()
        if "paper" in fname or "note" in fname:
            archetype = "paper_notepad"
        elif "split" in fname or "half" in fname:
            archetype = "split_events_phone"
        elif "grid" in fname or "2col" in fname:
            archetype = "bottom_2col_grid"
        elif "chart" in fname:
            archetype = "dark_card_chart"
        else:
            archetype = "dark_card_5row"

    return ARCHETYPE_RENDERERS.get(archetype, render_template_3)

def render_random_template(
    data: Dict[str, Any],
    output_path: str,
    template_name: Optional[str] = None,
    exclude_template: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders dynamic daily S3 data into one of the available templates chosen randomly.
    Dynamically scans templates/ so ANY newly added template is immediately available.
    Saves the final composite image to output_path.
    If exclude_template is provided, avoids repeating the last used template.
    """
    available = get_available_templates()
    if not available:
        raise FileNotFoundError(f"No template images found in {TEMPLATES_DIR}")

    if template_name and template_name in available:
        selected_tpl = template_name
    elif template_name and os.path.exists(os.path.join(TEMPLATES_DIR, template_name)):
        selected_tpl = template_name
    elif exclude_template and len(available) > 1:
        candidates = [t for t in available if t != exclude_template]
        selected_tpl = random.choice(candidates) if candidates else random.choice(available)
    else:
        selected_tpl = random.choice(available)

    tpl_path = os.path.join(TEMPLATES_DIR, selected_tpl)
    if not os.path.exists(tpl_path):
        raise FileNotFoundError(f"Template image not found: {tpl_path}")

    # Add formatted date if missing
    if "formatted_date" not in data:
        data["formatted_date"] = get_event_date_object(data).strftime("%a, %b %d, %Y (UTC)").upper()

    with Image.open(tpl_path) as base_img:
        img = base_img.convert("RGB")
        renderer = resolve_renderer_for_template(selected_tpl)
        if renderer:
            renderer(img, data)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "JPEG", quality=95)

    return {
        "template_used": selected_tpl,
        "output_path": output_path,
        "date": data["formatted_date"],
        "events_count": len(data.get("events", [])),
        "total_templates_available": len(available)
    }

def register_new_template(template_filename: str, layout_cfg: Optional[Dict[str, Any]] = None):
    """
    Registers or updates layout metadata for a new template design in layouts.json.
    """
    layouts = load_template_layouts()
    if layout_cfg:
        layouts[template_filename] = layout_cfg
    elif template_filename not in layouts:
        layouts[template_filename] = {
            "name": os.path.splitext(template_filename)[0].replace("_", " ").title(),
            "archetype": "dark_card_5row",
            "description": "Custom SpikeSignals template design"
        }
    save_template_layouts(layouts)
    return layouts[template_filename]
