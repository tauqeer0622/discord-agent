import os
import textwrap
from datetime import datetime
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

def get_font(size: int, bold: bool = False):
    """Loads TrueType font with cross-platform fallback (bundled fonts, Windows, Linux, Pillow default)."""
    bundled_dir = os.path.join(os.path.dirname(__file__), "templates", "fonts")
    bundled_names = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    for name in bundled_names:
        p = os.path.join(bundled_dir, name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass

    windows_names = (
        ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    )
    for name in windows_names:
        font_path = os.path.join("C:/Windows/Fonts", name)
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
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

def create_market_news_card(data: Dict[str, Any], output_path: str) -> str:
    """
    Renders a stunning 1200x675 Market News & Headline Summary Card from JSON data.
    Saves the final image to output_path.
    """
    width, height = 1200, 675
    img = Image.new("RGB", (width, height), color=(11, 15, 25))
    draw = ImageDraw.Draw(img)

    # 1. Background gradient simulation (top dark slate to deep navy)
    for y in range(height):
        r = int(11 + (15 - 11) * (y / height))
        g = int(15 + (23 - 15) * (y / height))
        b = int(25 + (38 - 25) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Outer border
    draw.rectangle([4, 4, width - 5, height - 5], outline=(30, 41, 59), width=2)

    # 2. Header Section
    font_brand = get_font(26, bold=True)
    font_sub = get_font(13, bold=False)
    font_badge = get_font(12, bold=True)
    font_headline = get_font(16, bold=True)
    font_stat_val = get_font(17, bold=True)
    font_stat_lbl = get_font(12, bold=False)
    font_footer = get_font(12, bold=False)

    # Brand Logo Pill
    draw.rounded_rectangle([32, 24, 160, 62], radius=6, fill=(14, 165, 233))
    draw.text((46, 31), "BR AXIS", font=font_brand, fill=(255, 255, 255))

    # Header Title
    title_text = data.get("title", "GLOBAL MARKET INTELLIGENCE").upper()
    draw.text((178, 27), title_text, font=get_font(19, bold=True), fill=(241, 245, 249))
    draw.text((180, 52), "LIVE 2-HOUR MARKET HEADLINES & LIQUIDITY FLOWS", font=font_sub, fill=(148, 163, 184))

    # Right Header Badges: Sentiment & Time
    sentiment = data.get("sentiment", "Bullish").upper()
    sent_score = data.get("sentiment_score", 65)
    sent_color = (16, 185, 129) if "BULL" in sentiment else ((239, 68, 68) if "BEAR" in sentiment else (14, 165, 233))
    sent_bg = (6, 78, 59) if "BULL" in sentiment else ((127, 29, 29) if "BEAR" in sentiment else (12, 74, 110))

    # Sentiment Pill
    sent_pill_text = f"SENTIMENT: {sentiment} ({sent_score}/100)"
    draw.rounded_rectangle([width - 430, 28, width - 210, 58], radius=6, fill=sent_bg, outline=sent_color, width=1)
    draw.text((width - 418, 35), sent_pill_text, font=font_badge, fill=sent_color)

    # Date / Time Pill
    time_str = f"{data.get('date', '2026-10-03')} • {data.get('time', '12:00 UTC')}"
    draw.rounded_rectangle([width - 195, 28, width - 32, 58], radius=6, fill=(30, 41, 59))
    draw.text((width - 182, 35), time_str, font=font_badge, fill=(203, 213, 225))

    # Horizontal glowing rule
    draw.line([(32, 76), (width - 32, 76)], fill=(30, 41, 59), width=1)
    draw.line([(32, 76), (180, 76)], fill=(14, 165, 233), width=2)

    # 3. Four Headline Story Cards
    headlines = data.get("headlines", [])[:4]
    start_y = 92
    card_h = 92
    gap = 12

    tag_colors = {
        "CRYPTO": ((14, 165, 233), (12, 74, 110)),
        "MACRO": ((168, 85, 247), (88, 28, 135)),
        "EQUITIES": ((59, 130, 246), (30, 58, 138)),
        "ON-CHAIN": ((16, 185, 129), (6, 78, 59)),
        "MARKET": ((234, 179, 8), (113, 63, 18))
    }

    for i, item in enumerate(headlines):
        cy = start_y + i * (card_h + gap)
        # Card Background
        draw.rounded_rectangle([32, cy, width - 32, cy + card_h], radius=8, fill=(19, 27, 46), outline=(30, 41, 59), width=1)

        category = (item.get("category") or "MARKET").upper()
        fg_col, bg_col = tag_colors.get(category, tag_colors["MARKET"])

        # Category Badge
        draw.rounded_rectangle([48, cy + 18, 140, cy + 46], radius=5, fill=bg_col)
        draw.text((56, cy + 24), category, font=font_badge, fill=fg_col)

        # Headline Text (Auto-wrapped)
        htext = item.get("title", "")
        wrapped_lines = textwrap.wrap(htext, width=74)[:2]
        line_y = cy + 16 if len(wrapped_lines) > 1 else cy + 24
        for line in wrapped_lines:
            draw.text((156, line_y), line, font=font_headline, fill=(241, 245, 249))
            line_y += 24

        # Impact Badge (Right Side)
        impact = (item.get("impact") or "Neutral").capitalize()
        imp_fg = (16, 185, 129) if "Bull" in impact else ((239, 68, 68) if "Bear" in impact else (148, 163, 184))
        draw.text((width - 150, cy + 34), f"• {impact}", font=font_badge, fill=imp_fg)

    # 4. Bottom Metrics Bar (Key Stats)
    bar_y = 520
    stats = data.get("key_stats", [])[:4]
    if stats:
        widget_w = (width - 64 - (len(stats) - 1) * 14) // len(stats)
        for i, s in enumerate(stats):
            wx = 32 + i * (widget_w + 14)
            draw.rounded_rectangle([wx, bar_y, wx + widget_w, bar_y + 68], radius=6, fill=(15, 23, 42), outline=(30, 41, 59), width=1)
            
            # Label
            draw.text((wx + 16, bar_y + 12), s.get("label", "Asset"), font=font_stat_lbl, fill=(148, 163, 184))
            
            # Value
            val_text = s.get("value", "$0.00")
            draw.text((wx + 16, bar_y + 34), val_text, font=font_stat_val, fill=(255, 255, 255))
            
            # Change percentage
            chg = s.get("change", "+0.0%")
            chg_fg = (16, 185, 129) if chg.startswith("+") else ((239, 68, 68) if chg.startswith("-") else (148, 163, 184))
            draw.text((wx + widget_w - 75, bar_y + 36), chg, font=font_badge, fill=chg_fg)

    # 5. Footer Line & Branding
    draw.line([(32, 608), (width - 32, 608)], fill=(30, 41, 59), width=1)
    draw.text((34, 622), "BR Axis Market Terminal • Real-Time Automated Intel", font=font_footer, fill=(100, 116, 139))
    draw.text((width - 350, 622), "Join VIP Trading Community: discord.gg/4av5x7Faa3", font=font_footer, fill=(14, 165, 233))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "JPEG", quality=95)
    return output_path
