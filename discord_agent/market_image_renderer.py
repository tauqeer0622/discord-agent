import html
import json
import logging
import re
import os
import random
from copy import deepcopy
from datetime import datetime, timezone

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


DEFAULT_MARKET_IMAGE_WEBADDRESS = "https://www.spikesignals.com/"
MARKET_IMAGE_COMPANY_WEBADDRESS = (
    os.getenv("MARKET_IMAGE_COMPANY_WEBADDRESS", "")
    .strip()
    .strip('"')
    .strip("'")
    or DEFAULT_MARKET_IMAGE_WEBADDRESS
)
MARKET_IMAGE_BRAND_NAME = (
    os.getenv("MARKET_IMAGE_BRAND_NAME", "")
    .strip()
    .strip('"')
    .strip("'")
    or DEFAULT_MARKET_IMAGE_WEBADDRESS
)

SAMPLE_MARKET_DATA = {
    "headline": "TODAY WAS A RED DAY",
    "date_line": "JULY 17, 2026 - MARKET SUMMARY",
    "theme": "red",
    "market_cards": [
        {
            "label": "NASDAQ",
            "value": "-4.2%",
            "chart": [82, 76, 70, 72, 67, 63, 61, 58, 51, 46, 39],
        },
        {
            "label": "S&P 500",
            "value": "-2.6%",
            "chart": [78, 72, 68, 64, 66, 61, 57, 54, 48, 42, 35],
        },
        {
            "label": "SEMICONDUCTOR SECTOR",
            "value": "-6.1%",
            "note": "Biggest drop since 2020",
            "chart": [88, 75, 68, 67, 66, 64, 63, 61, 55, 50, 42],
        },
    ],
    "summary_text": "Stronger-than-expected jobs report increased fears that the Fed may need to raise rates again.",
    "jobs": {
        "label": "JOBS ADDED",
        "value": "172,000",
        "note": "vs. 80,000 expected",
    },
    "losers_title": "5 BIGGEST LOSERS (TODAY)",
    "losers": [
        {"ticker": "MRVL", "name": "MARVELL TECHNOLOGY", "value": "-16%"},
        {"ticker": "MU", "name": "MICRON TECHNOLOGY", "value": "-13%"},
        {"ticker": "INTC", "name": "INTEL", "value": "-11%"},
        {"ticker": "AMD", "name": "ADVANCED MICRO DEVICES", "value": "-11%"},
        {"ticker": "AVGO", "name": "BROADCOM", "value": "-7% to -13%"},
    ],
    "reasons": [
        {
            "icon": "chart",
            "title": "Hot jobs report",
            "body": "172,000 jobs added vs. expectations around 80,000. Fears of fewer rate cuts or another rate hike.",
        },
        {
            "icon": "crowd",
            "title": "AI stocks became crowded",
            "body": "Semiconductors rallied massively in recent months. Traders took profits all at once.",
        },
        {
            "icon": "earnings",
            "title": "Broadcom earnings",
            "body": "Results were strong, but expectations were even higher. Broadcom's decline triggered selling across AI names.",
        },
    ],
    "scenarios": [
        {
            "title": "SCENARIO 1 (MOST LIKELY - 55%)",
            "subtitle": "RELIEF BOUNCE MONDAY-TUESDAY",
            "color": "green",
            "points": [
                "QQQ rebounds 1-3%",
                "Semis recover part of Friday's loss",
                "NVDA, AVGO, AMD attract dip buyers",
            ],
        },
        {
            "title": "SCENARIO 2 (30%)",
            "subtitle": "CONSOLIDATION WEEK",
            "color": "amber",
            "points": [
                "QQQ trades sideways",
                "Semiconductor stocks remain volatile",
                "Investors reassess Fed expectations",
            ],
        },
        {
            "title": "SCENARIO 3 (15%)",
            "subtitle": "SECOND LEG LOWER",
            "color": "red",
            "points": [
                "Nasdaq drops another 3-5%",
                "Semis fall another 5-10%",
                "Triggered if bond yields continue rising",
            ],
        },
    ],
    "probabilities": [
        {"label": "BULLISH REBOUND", "value": "55%", "color": "green"},
        {"label": "SIDEWAYS/CHOPPY", "value": "30%", "color": "amber"},
        {"label": "CONTINUED SELLOFF", "value": "15%", "color": "red"},
    ],
    "bottom_line": "Today was a panic day. Next week is about reaction, not fundamentals.",
    "disclaimer": "This content is for informational purposes only and not financial advice.",
}

SAMPLE_MARKET_DATA_VARIANTS = [
    SAMPLE_MARKET_DATA,
    {
        **SAMPLE_MARKET_DATA,
        "headline": "TODAY WAS A VOLATILE DAY",
        "market_cards": [
            {
                "label": "NASDAQ",
                "value": "-1.7%",
                "chart": [70, 76, 69, 73, 62, 66, 59, 61, 54, 48, 44],
            },
            {
                "label": "S&P 500",
                "value": "-0.9%",
                "chart": [66, 69, 65, 67, 62, 60, 63, 58, 55, 52, 49],
            },
            {
                "label": "MEGA CAP TECH",
                "value": "-2.3%",
                "note": "AI leaders pulled back",
                "chart": [82, 78, 74, 76, 69, 65, 67, 60, 56, 50, 45],
            },
        ],
        "summary_text": "Markets faded after an early bounce as traders rotated out of crowded growth names.",
        "jobs": {
            "label": "10Y YIELD",
            "value": "4.31%",
            "note": "higher on the day",
        },
        "losers": [
            {"ticker": "NVDA", "name": "NVIDIA", "value": "-5%"},
            {"ticker": "TSLA", "name": "TESLA", "value": "-4%"},
            {"ticker": "PLTR", "name": "PALANTIR", "value": "-6%"},
            {"ticker": "SMCI", "name": "SUPER MICRO", "value": "-8%"},
            {"ticker": "ARM", "name": "ARM HOLDINGS", "value": "-5%"},
        ],
        "reasons": [
            {
                "icon": "chart",
                "title": "Yield pressure",
                "body": "Bond yields moved higher, reducing appetite for expensive growth stocks.",
            },
            {
                "icon": "crowd",
                "title": "Crowded AI trade",
                "body": "The biggest winners saw profit-taking as traders reduced risk into the close.",
            },
            {
                "icon": "earnings",
                "title": "Guidance worries",
                "body": "Investors focused on whether strong demand can keep matching high expectations.",
            },
        ],
        "bottom_line": "The move looked more like positioning stress than a full breakdown.",
    },
    {
        **SAMPLE_MARKET_DATA,
        "headline": "TODAY WAS A CHOPPY DAY",
        "market_cards": [
            {
                "label": "NASDAQ",
                "value": "-0.6%",
                "chart": [55, 60, 54, 61, 57, 63, 58, 55, 59, 53, 51],
            },
            {
                "label": "S&P 500",
                "value": "+0.1%",
                "chart": [50, 52, 49, 54, 51, 55, 53, 56, 54, 57, 58],
            },
            {
                "label": "RUSSELL 2000",
                "value": "-1.4%",
                "note": "Small caps lagged",
                "chart": [68, 62, 66, 59, 57, 60, 54, 50, 52, 47, 43],
            },
        ],
        "summary_text": "Indexes finished mixed as defensive sectors held up while speculative names sold off.",
        "jobs": {
            "label": "VIX",
            "value": "18.7",
            "note": "risk premium rose",
        },
        "losers": [
            {"ticker": "COIN", "name": "COINBASE", "value": "-7%"},
            {"ticker": "MSTR", "name": "MICROSTRATEGY", "value": "-6%"},
            {"ticker": "RIVN", "name": "RIVIAN", "value": "-5%"},
            {"ticker": "SOFI", "name": "SOFI", "value": "-4%"},
            {"ticker": "HOOD", "name": "ROBINHOOD", "value": "-5%"},
        ],
        "reasons": [
            {
                "icon": "chart",
                "title": "Risk-off rotation",
                "body": "Money moved away from high beta names and into steadier sectors.",
            },
            {
                "icon": "crowd",
                "title": "Weak breadth",
                "body": "A few large caps supported the index while many smaller names declined.",
            },
            {
                "icon": "earnings",
                "title": "Crypto weakness",
                "body": "Digital asset-linked stocks fell as traders reduced leverage.",
            },
        ],
        "bottom_line": "The headline index move was mild, but under the surface risk appetite weakened.",
    },
    {
        **SAMPLE_MARKET_DATA,
        "headline": "TODAY WAS A SELL-OFF DAY",
        "market_cards": [
            {
                "label": "DOW JONES",
                "value": "-1.1%",
                "chart": [78, 75, 72, 74, 68, 64, 66, 60, 56, 53, 49],
            },
            {
                "label": "S&P 500",
                "value": "-1.9%",
                "chart": [84, 80, 78, 72, 70, 67, 62, 58, 54, 49, 42],
            },
            {
                "label": "BANKING SECTOR",
                "value": "-3.2%",
                "note": "Regional banks under pressure",
                "chart": [74, 71, 68, 62, 64, 58, 52, 48, 45, 40, 36],
            },
        ],
        "summary_text": "Financials dragged the market lower after credit concerns returned to the front page.",
        "jobs": {
            "label": "OIL",
            "value": "-2.8%",
            "note": "growth concerns hit demand",
        },
        "losers": [
            {"ticker": "KRE", "name": "REGIONAL BANK ETF", "value": "-5%"},
            {"ticker": "JPM", "name": "JPMORGAN", "value": "-2%"},
            {"ticker": "BAC", "name": "BANK OF AMERICA", "value": "-3%"},
            {"ticker": "WFC", "name": "WELLS FARGO", "value": "-3%"},
            {"ticker": "C", "name": "CITIGROUP", "value": "-2%"},
        ],
        "reasons": [
            {
                "icon": "chart",
                "title": "Credit concerns",
                "body": "Traders reacted to renewed worries around loan losses and slower growth.",
            },
            {
                "icon": "crowd",
                "title": "Cyclical weakness",
                "body": "Banks, energy, and small caps all lagged as investors reduced economic exposure.",
            },
            {
                "icon": "earnings",
                "title": "Lower guidance",
                "body": "Management commentary hinted at softer demand and tighter consumer spending.",
            },
        ],
        "bottom_line": "This was a classic defensive rotation with financials as the weak link.",
    },
]

VISUAL_STYLES = [
    # ── Original 4 ───────────────────────────────────────────────────────────
    {
        "name": "classic",
        "accent": "#e11d2e",
        "accent_soft": "rgba(225,29,46,.24)",
        "panel": "linear-gradient(180deg, rgba(95,0,0,.5), rgba(8,0,0,.7))",
        "bg": "radial-gradient(circle at 50% 20%, rgba(140,0,0,.2), transparent 28%), linear-gradient(180deg, #070202, #020101 70%)",
        "layout": "layout-classic",
        "title_align": "center",
        "card_shape": "9px",
        "font": "Inter",
        "font_url": "https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap",
    },
    {
        "name": "terminal",
        "accent": "#20d17b",
        "accent_soft": "rgba(32,209,123,.18)",
        "panel": "linear-gradient(180deg, rgba(0,62,39,.46), rgba(3,14,12,.82))",
        "bg": "linear-gradient(135deg, #02110d, #080b10 46%, #020504), repeating-linear-gradient(0deg, rgba(255,255,255,.035) 0 1px, transparent 1px 8px)",
        "layout": "layout-terminal",
        "title_align": "left",
        "card_shape": "2px",
        "font": "JetBrains Mono",
        "font_url": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap",
    },
    {
        "name": "amber",
        "accent": "#f0b93d",
        "accent_soft": "rgba(240,185,61,.18)",
        "panel": "linear-gradient(180deg, rgba(86,55,0,.5), rgba(15,9,2,.82))",
        "bg": "radial-gradient(circle at 82% 12%, rgba(240,185,61,.2), transparent 24%), linear-gradient(160deg, #090602, #130d06 44%, #020101)",
        "layout": "layout-report",
        "title_align": "left",
        "card_shape": "0",
        "font": "Outfit",
        "font_url": "https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&display=swap",
    },
    {
        "name": "blueprint",
        "accent": "#38bdf8",
        "accent_soft": "rgba(56,189,248,.18)",
        "panel": "linear-gradient(180deg, rgba(9,38,68,.54), rgba(2,9,18,.86))",
        "bg": "linear-gradient(180deg, #061525, #020714), linear-gradient(90deg, rgba(56,189,248,.06) 1px, transparent 1px), linear-gradient(0deg, rgba(56,189,248,.06) 1px, transparent 1px)",
        "layout": "layout-blueprint",
        "title_align": "center",
        "card_shape": "14px",
        "font": "Space Grotesk",
        "font_url": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap",
    },
    # ── 12 New Styles ─────────────────────────────────────────────────────────
    {
        # Cyan/purple neon glow on near-black.
        "name": "neon",
        "accent": "#00f5ff",
        "accent_soft": "rgba(0,245,255,.15)",
        "panel": "linear-gradient(180deg, rgba(0,50,70,.55), rgba(0,10,20,.88))",
        "bg": "radial-gradient(ellipse at 30% 0%, rgba(0,245,255,.12), transparent 40%), radial-gradient(ellipse at 70% 100%, rgba(120,0,255,.12), transparent 40%), linear-gradient(180deg, #010810, #030212)",
        "layout": "layout-neon",
        "title_align": "center",
        "card_shape": "4px",
        "font": "Exo 2",
        "font_url": "https://fonts.googleapis.com/css2?family=Exo+2:wght@400;700;900&display=swap",
    },
    {
        # Ice-blue on deep navy. Rounded cards, cool and clean.
        "name": "glacier",
        "accent": "#7ec8e3",
        "accent_soft": "rgba(126,200,227,.16)",
        "panel": "linear-gradient(180deg, rgba(20,60,90,.52), rgba(5,15,30,.86))",
        "bg": "linear-gradient(135deg, #040e1a 0%, #071828 50%, #040e1a 100%), repeating-linear-gradient(45deg, rgba(126,200,227,.025) 0 1px, transparent 1px 28px)",
        "layout": "layout-report",
        "title_align": "left",
        "card_shape": "16px",
        "font": "Nunito",
        "font_url": "https://fonts.googleapis.com/css2?family=Nunito:wght@400;700;900&display=swap",
    },
    {
        # Gold serif on deep navy. Luxury editorial with left-rule headline.
        "name": "midnight",
        "accent": "#d4af37",
        "accent_soft": "rgba(212,175,55,.22)",
        "panel": "linear-gradient(180deg, rgba(40,30,0,.48), rgba(5,4,1,.9))",
        "bg": "radial-gradient(ellipse at 50% -20%, rgba(30,20,0,.8), transparent 60%), linear-gradient(180deg, #03020a, #070410)",
        "layout": "layout-midnight",
        "title_align": "left",
        "card_shape": "0px",
        "font": "Cormorant Garamond",
        "font_url": "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,600;0,700;1,600&display=swap",
    },
    {
        # Rose-gold serif on deep burgundy. Dramatic and premium.
        "name": "crimson",
        "accent": "#c9a96e",
        "accent_soft": "rgba(201,169,110,.22)",
        "panel": "linear-gradient(180deg, rgba(80,0,20,.52), rgba(15,2,5,.9))",
        "bg": "radial-gradient(circle at 50% 0%, rgba(150,0,40,.22), transparent 50%), linear-gradient(160deg, #0a0204, #150408)",
        "layout": "layout-classic",
        "title_align": "center",
        "card_shape": "6px",
        "font": "Playfair Display",
        "font_url": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&display=swap",
    },
    {
        # Bright green monospace on pure black. Hacker terminal aesthetic.
        "name": "matrix",
        "accent": "#39ff14",
        "accent_soft": "rgba(57,255,20,.12)",
        "panel": "linear-gradient(180deg, rgba(0,40,10,.58), rgba(0,8,2,.94))",
        "bg": "linear-gradient(180deg, #000800, #010d01), repeating-linear-gradient(0deg, rgba(57,255,20,.03) 0 1px, transparent 1px 6px), repeating-linear-gradient(90deg, rgba(57,255,20,.015) 0 1px, transparent 1px 60px)",
        "layout": "layout-terminal",
        "title_align": "left",
        "card_shape": "0px",
        "font": "Share Tech Mono",
        "font_url": "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap",
    },
    {
        # Lava orange on near-black. Bold condensed font, magazine layout.
        "name": "volcanic",
        "accent": "#ff6b35",
        "accent_soft": "rgba(255,107,53,.22)",
        "panel": "linear-gradient(180deg, rgba(60,20,0,.58), rgba(12,4,0,.9))",
        "bg": "radial-gradient(circle at 50% 110%, rgba(255,107,53,.2), transparent 40%), radial-gradient(circle at 50% 0%, rgba(255,200,0,.06), transparent 30%), linear-gradient(180deg, #080200, #0f0400)",
        "layout": "layout-magazine",
        "title_align": "center",
        "card_shape": "3px",
        "font": "Barlow Condensed",
        "font_url": "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;700;900&display=swap",
    },
    {
        # Purple-to-teal aurora gradient. Glassmorphism frosted panels.
        "name": "aurora",
        "accent": "#c77dff",
        "accent_soft": "rgba(199,125,255,.22)",
        "panel": "linear-gradient(135deg, rgba(60,0,100,.32), rgba(0,40,80,.32), rgba(5,3,15,.65))",
        "bg": "radial-gradient(ellipse at 0% 0%, rgba(199,125,255,.2), transparent 42%), radial-gradient(ellipse at 100% 100%, rgba(0,210,211,.16), transparent 42%), radial-gradient(ellipse at 50% 50%, rgba(80,0,120,.1), transparent 60%), linear-gradient(180deg, #030008, #03030e)",
        "layout": "layout-aurora",
        "title_align": "center",
        "card_shape": "20px",
        "font": "Raleway",
        "font_url": "https://fonts.googleapis.com/css2?family=Raleway:wght@400;700;900&display=swap",
    },
    {
        # Rose-pink on near-black. Blueprint layout with pink accent.
        "name": "rose",
        "accent": "#ff6b9d",
        "accent_soft": "rgba(255,107,157,.2)",
        "panel": "linear-gradient(180deg, rgba(80,0,35,.48), rgba(12,2,8,.9))",
        "bg": "radial-gradient(circle at 80% 20%, rgba(255,107,157,.16), transparent 36%), radial-gradient(circle at 20% 80%, rgba(180,0,80,.08), transparent 36%), linear-gradient(150deg, #080205, #0f0308)",
        "layout": "layout-blueprint",
        "title_align": "center",
        "card_shape": "12px",
        "font": "Josefin Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=Josefin+Sans:wght@400;600;700&display=swap",
    },
    {
        # Bright gold on dark warm. High energy, report layout.
        "name": "solar",
        "accent": "#ffc300",
        "accent_soft": "rgba(255,195,0,.22)",
        "panel": "linear-gradient(180deg, rgba(70,40,0,.52), rgba(12,7,0,.9))",
        "bg": "radial-gradient(ellipse at 50% -10%, rgba(255,195,0,.2), transparent 42%), linear-gradient(180deg, #080501, #0e0900)",
        "layout": "layout-report",
        "title_align": "left",
        "card_shape": "4px",
        "font": "Oswald",
        "font_url": "https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&display=swap",
    },
    {
        # Steel cobalt on dark navy. Blueprint layout, corporate precision.
        "name": "cobalt",
        "accent": "#4a90d9",
        "accent_soft": "rgba(74,144,217,.22)",
        "panel": "linear-gradient(180deg, rgba(0,20,60,.58), rgba(0,4,15,.9))",
        "bg": "linear-gradient(135deg, #010814, #020a1e), repeating-linear-gradient(90deg, rgba(74,144,217,.04) 0 1px, transparent 1px 36px), repeating-linear-gradient(0deg, rgba(74,144,217,.04) 0 1px, transparent 1px 36px)",
        "layout": "layout-blueprint",
        "title_align": "center",
        "card_shape": "8px",
        "font": "IBM Plex Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;700&display=swap",
    },
    {
        # Vivid emerald on dark forest. Classic layout, fresh and sharp.
        "name": "emerald",
        "accent": "#2ecc71",
        "accent_soft": "rgba(46,204,113,.2)",
        "panel": "linear-gradient(180deg, rgba(0,50,20,.58), rgba(2,10,6,.9))",
        "bg": "radial-gradient(circle at 20% 80%, rgba(46,204,113,.14), transparent 40%), radial-gradient(circle at 80% 10%, rgba(0,100,50,.1), transparent 32%), linear-gradient(180deg, #020b05, #030e07)",
        "layout": "layout-classic",
        "title_align": "center",
        "card_shape": "8px",
        "font": "DM Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;700;900&display=swap",
    },
    {
        # Pure black with light-gray accent. Ultra-minimal surgical clarity.
        "name": "onyx",
        "accent": "#e8e8e8",
        "accent_soft": "rgba(232,232,232,.1)",
        "panel": "linear-gradient(180deg, rgba(35,35,35,.52), rgba(8,8,8,.88))",
        "bg": "linear-gradient(180deg, #080808, #000000)",
        "layout": "layout-minimal",
        "title_align": "center",
        "card_shape": "1px",
        "font": "Plus Jakarta Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;700;800&display=swap",
    },
    # ── 14 New Styles (total: 30) ──────────────────────────────────────────────
    {
        # Heat-map orange-red on near-black. Matrix scan-line background.
        "name": "infrared",
        "accent": "#ff3c00",
        "accent_soft": "rgba(255,60,0,.2)",
        "panel": "linear-gradient(180deg, rgba(80,15,0,.55), rgba(10,2,0,.9))",
        "bg": "radial-gradient(circle at 50% 0%, rgba(255,60,0,.18), transparent 40%), linear-gradient(180deg, #0a0100, #0f0200), repeating-linear-gradient(0deg, rgba(255,60,0,.025) 0 1px, transparent 1px 8px)",
        "layout": "layout-magazine",
        "title_align": "center",
        "card_shape": "2px",
        "font": "Chakra Petch",
        "font_url": "https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;700&display=swap",
    },
    {
        # Electric purple on near-black. Neon layout with orbital font.
        "name": "plasma",
        "accent": "#d400ff",
        "accent_soft": "rgba(212,0,255,.18)",
        "panel": "linear-gradient(180deg, rgba(70,0,100,.52), rgba(8,0,14,.9))",
        "bg": "radial-gradient(ellipse at 20% 0%, rgba(212,0,255,.18), transparent 38%), radial-gradient(ellipse at 80% 100%, rgba(0,150,255,.12), transparent 38%), linear-gradient(180deg, #050009, #02000e)",
        "layout": "layout-neon",
        "title_align": "center",
        "card_shape": "6px",
        "font": "Orbitron",
        "font_url": "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&display=swap",
    },
    {
        # Steel blue on graphite. Blueprint feel, cool corporate precision.
        "name": "obsidian",
        "accent": "#8ecae6",
        "accent_soft": "rgba(142,202,230,.18)",
        "panel": "linear-gradient(180deg, rgba(10,30,50,.56), rgba(4,8,14,.9))",
        "bg": "linear-gradient(160deg, #060c14, #0a1220, #060c14), repeating-linear-gradient(90deg, rgba(142,202,230,.03) 0 1px, transparent 1px 40px)",
        "layout": "layout-blueprint",
        "title_align": "center",
        "card_shape": "10px",
        "font": "Syne",
        "font_url": "https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&display=swap",
    },
    {
        # Warm gold on deep dark. Editorial report layout.
        "name": "citrine",
        "accent": "#e9c46a",
        "accent_soft": "rgba(233,196,106,.2)",
        "panel": "linear-gradient(180deg, rgba(60,42,0,.52), rgba(12,9,0,.9))",
        "bg": "radial-gradient(ellipse at 60% 0%, rgba(233,196,106,.14), transparent 40%), linear-gradient(180deg, #080601, #0e0b02)",
        "layout": "layout-report",
        "title_align": "left",
        "card_shape": "6px",
        "font": "Poppins",
        "font_url": "https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap",
    },
    {
        # Arctic ice blue on near-black. Ultra-clean monospace minimal.
        "name": "tundra",
        "accent": "#90e0ef",
        "accent_soft": "rgba(144,224,239,.15)",
        "panel": "linear-gradient(180deg, rgba(8,30,45,.5), rgba(2,8,14,.88))",
        "bg": "linear-gradient(135deg, #030d14, #050f1a, #020a10), repeating-linear-gradient(135deg, rgba(144,224,239,.02) 0 1px, transparent 1px 28px)",
        "layout": "layout-minimal",
        "title_align": "center",
        "card_shape": "0px",
        "font": "Roboto Mono",
        "font_url": "https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;700&display=swap",
    },
    {
        # Rust red on dark terrain. Bold condensed magazine look.
        "name": "mars",
        "accent": "#c1440e",
        "accent_soft": "rgba(193,68,14,.22)",
        "panel": "linear-gradient(180deg, rgba(70,20,5,.56), rgba(12,4,1,.9))",
        "bg": "radial-gradient(circle at 30% 20%, rgba(193,68,14,.18), transparent 40%), linear-gradient(160deg, #0a0302, #100503)",
        "layout": "layout-magazine",
        "title_align": "center",
        "card_shape": "3px",
        "font": "Anton",
        "font_url": "https://fonts.googleapis.com/css2?family=Anton&display=swap",
    },
    {
        # Electric cyan on dark navy. Blueprint data-dashboard precision.
        "name": "pulse",
        "accent": "#00b4d8",
        "accent_soft": "rgba(0,180,216,.18)",
        "panel": "linear-gradient(180deg, rgba(0,36,58,.52), rgba(0,6,12,.9))",
        "bg": "linear-gradient(180deg, #000d14, #001018), linear-gradient(90deg, rgba(0,180,216,.05) 1px, transparent 1px), linear-gradient(0deg, rgba(0,180,216,.05) 1px, transparent 1px)",
        "layout": "layout-blueprint",
        "title_align": "center",
        "card_shape": "6px",
        "font": "IBM Plex Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;700&display=swap",
    },
    {
        # Acid lime on deep charcoal. Hacker-finance terminal aesthetic.
        "name": "heist",
        "accent": "#caffbf",
        "accent_soft": "rgba(202,255,191,.12)",
        "panel": "linear-gradient(180deg, rgba(10,40,14,.52), rgba(2,10,4,.92))",
        "bg": "linear-gradient(180deg, #020d04, #010802), repeating-linear-gradient(0deg, rgba(202,255,191,.025) 0 1px, transparent 1px 7px)",
        "layout": "layout-terminal",
        "title_align": "left",
        "card_shape": "0px",
        "font": "Share Tech Mono",
        "font_url": "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap",
    },
    {
        # Sunset amber-orange. Warm luxury midnight editorial.
        "name": "dusk",
        "accent": "#f4a261",
        "accent_soft": "rgba(244,162,97,.2)",
        "panel": "linear-gradient(180deg, rgba(70,35,5,.52), rgba(12,6,1,.9))",
        "bg": "radial-gradient(ellipse at 80% 0%, rgba(244,162,97,.18), transparent 40%), radial-gradient(ellipse at 20% 100%, rgba(200,50,0,.1), transparent 40%), linear-gradient(180deg, #090401, #0e0600)",
        "layout": "layout-midnight",
        "title_align": "left",
        "card_shape": "4px",
        "font": "Playfair Display",
        "font_url": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&display=swap",
    },
    {
        # Deep violet on near-black. Aurora frosted-glass panels.
        "name": "indigo",
        "accent": "#7b2d8b",
        "accent_soft": "rgba(123,45,139,.22)",
        "panel": "linear-gradient(135deg, rgba(50,0,80,.38), rgba(10,0,20,.72), rgba(4,2,12,.85))",
        "bg": "radial-gradient(ellipse at 10% 10%, rgba(123,45,139,.22), transparent 42%), radial-gradient(ellipse at 90% 90%, rgba(60,0,120,.16), transparent 42%), linear-gradient(180deg, #040008, #03000c)",
        "layout": "layout-aurora",
        "title_align": "center",
        "card_shape": "20px",
        "font": "Raleway",
        "font_url": "https://fonts.googleapis.com/css2?family=Raleway:wght@400;700;900&display=swap",
    },
    {
        # Cool grey-teal on graphite. Surgical minimal precision.
        "name": "graphene",
        "accent": "#a8dadc",
        "accent_soft": "rgba(168,218,220,.14)",
        "panel": "linear-gradient(180deg, rgba(18,28,30,.52), rgba(5,8,9,.9))",
        "bg": "linear-gradient(180deg, #050909, #070c0c)",
        "layout": "layout-minimal",
        "title_align": "center",
        "card_shape": "2px",
        "font": "DM Sans",
        "font_url": "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;700;900&display=swap",
    },
    {
        # Bright warm yellow on near-black. High-voltage volcanic energy.
        "name": "flare",
        "accent": "#ffb703",
        "accent_soft": "rgba(255,183,3,.22)",
        "panel": "linear-gradient(180deg, rgba(80,50,0,.56), rgba(12,8,0,.9))",
        "bg": "radial-gradient(ellipse at 50% -10%, rgba(255,183,3,.22), transparent 42%), radial-gradient(circle at 50% 110%, rgba(255,100,0,.12), transparent 40%), linear-gradient(180deg, #090600, #0e0900)",
        "layout": "layout-magazine",
        "title_align": "center",
        "card_shape": "4px",
        "font": "Barlow Condensed",
        "font_url": "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;700;900&display=swap",
    },
    {
        # Warm pink-red on deep dark. Classic layout, intimate editorial.
        "name": "coral",
        "accent": "#f28482",
        "accent_soft": "rgba(242,132,130,.2)",
        "panel": "linear-gradient(180deg, rgba(80,20,18,.52), rgba(14,4,4,.9))",
        "bg": "radial-gradient(circle at 70% 10%, rgba(242,132,130,.16), transparent 38%), radial-gradient(circle at 30% 90%, rgba(180,30,30,.08), transparent 36%), linear-gradient(160deg, #080202, #0e0404)",
        "layout": "layout-classic",
        "title_align": "center",
        "card_shape": "10px",
        "font": "Nunito",
        "font_url": "https://fonts.googleapis.com/css2?family=Nunito:wght@400;700;900&display=swap",
    },
    {
        # Fresh medium green on deep forest. Report layout, growth vibes.
        "name": "verdant",
        "accent": "#52b788",
        "accent_soft": "rgba(82,183,136,.2)",
        "panel": "linear-gradient(180deg, rgba(0,45,22,.52), rgba(1,10,6,.9))",
        "bg": "radial-gradient(circle at 30% 80%, rgba(82,183,136,.14), transparent 40%), radial-gradient(circle at 70% 10%, rgba(0,80,40,.1), transparent 32%), linear-gradient(180deg, #020a05, #030e07)",
        "layout": "layout-report",
        "title_align": "left",
        "card_shape": "8px",
        "font": "Outfit",
        "font_url": "https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&display=swap",
    },
]


def _esc(value):
    return html.escape(str(value or ""), quote=True)


def _list_items(items):
    return "".join(f"<li>{_esc(item)}</li>" for item in items or [])


def _sparkline(points, color="#e11d2e", uid="0", stroke_width=5):
    """SVG sparkline with gradient area fill beneath the line."""
    values = points or [70, 62, 58, 52, 44]
    min_v = min(values)
    max_v = max(values)
    spread = max(max_v - min_v, 1)
    coords = []
    for index, value in enumerate(values):
        x = 8 + index * (284 / max(len(values) - 1, 1))
        y = 88 - ((value - min_v) / spread * 70)
        coords.append((x, y))
    poly_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    first_x  = f"{coords[0][0]:.1f}"
    last_x   = f"{coords[-1][0]:.1f}"
    area_d   = (
        "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        + f" L {last_x},100 L {first_x},100 Z"
    )
    grad_id = f"spgrad-{uid}"
    return (
        f'<svg class="sparkline" viewBox="0 0 300 100" preserveAspectRatio="none">'
        f'<defs>'
        f'<linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.38"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<path d="{area_d}" fill="url(#{grad_id})"/>'
        f'<polyline points="{poly_pts}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _reason_icon(kind):
    labels = {
        "chart": "▥",
        "crowd": "●",
        "earnings": "$",
    }
    return labels.get(kind, "!")


def _visual_style(data):
    # LLM-generated style (fully custom) takes priority over named presets.
    generated = data.get("_generated_style")
    if generated and isinstance(generated, dict):
        return generated
    wanted = str(data.get("visual_style") or "").strip().lower()
    if wanted:
        for style in VISUAL_STYLES:
            if style["name"] == wanted:
                return style
    return random.choice(VISUAL_STYLES)


def _section_title_css(style: str, accent: str) -> str:
    """Return extra inline CSS for .section-title based on the chosen micro-variation."""
    if style == "bottom-border":
        return f"border-bottom: 2px solid {accent}; padding-bottom: 8px;"
    if style == "left-pill":
        return f"border-left: 5px solid {accent}; padding-left: 14px; text-align: left;"
    if style == "dot-leader":
        return f"letter-spacing: 4px; color: {accent};"
    if style == "overline":
        return f"border-top: 2px solid {accent}; padding-top: 8px;"
    return ""  # plain


def _build_bg_pattern(pattern: str, accent_soft: str) -> str:
    """
    Return a CSS background-image value string for decorative overlay patterns.
    Designed to be prepended to the main 'bg' gradient so it sits on top.
    """
    if pattern == "grid":
        return (
            f"repeating-linear-gradient(90deg, {accent_soft} 0 1px, transparent 1px 38px), "
            f"repeating-linear-gradient(0deg, {accent_soft} 0 1px, transparent 1px 38px)"
        )
    if pattern == "dots":
        return (
            f"radial-gradient(circle, {accent_soft} 1.2px, transparent 1.2px)"
        )
    if pattern == "diagonal":
        return (
            f"repeating-linear-gradient(45deg, {accent_soft} 0 1px, transparent 1px 20px), "
            f"repeating-linear-gradient(-45deg, {accent_soft} 0 1px, transparent 1px 20px)"
        )
    if pattern == "scanlines":
        return f"repeating-linear-gradient(0deg, rgba(0,0,0,.22) 0 2px, transparent 2px 5px)"
    if pattern == "hex":
        return (
            f"repeating-linear-gradient(60deg, {accent_soft} 0 1px, transparent 1px 22px), "
            f"repeating-linear-gradient(-60deg, {accent_soft} 0 1px, transparent 1px 22px), "
            f"repeating-linear-gradient(0deg, {accent_soft} 0 1px, transparent 1px 22px)"
        )
    if pattern == "crosshatch":
        return (
            f"repeating-linear-gradient(90deg, {accent_soft} 0 1px, transparent 1px 12px), "
            f"repeating-linear-gradient(0deg, {accent_soft} 0 1px, transparent 1px 12px)"
        )
    return ""  # none


def _random_micro_variations() -> dict:
    """
    Return a fresh dict of randomly chosen per-render visual micro-variations.
    Every call to build_market_image_html() picks a new set, so even two renders
    using the same named style + layout will look noticeably different.
    """
    return {
        "sparkline_stroke":    random.choice([3, 4, 5, 6, 7]),
        "headline_spacing":    random.choice(["-1px", "0px", "2px", "4px", "6px"]),
        "headline_weight":     random.choice(["700", "800", "900"]),
        "section_title_style": random.choice(["plain", "bottom-border", "left-pill", "dot-leader", "overline"]),
        "footer_justify":      random.choice(["space-between", "center", "flex-end"]),
        "loser_radius":        random.choice(["0px", "4px", "8px", "50px"]),
        "icon_radius":         random.choice(["0px", "6px", "14px", "50%"]),
        "grid_gap":            random.choice(["10px", "12px", "16px", "20px"]),
        "card_height":         random.choice(["195px", "210px", "225px", "240px"]),
        "subhead_style":       random.choice(["normal", "italic"]),
        "metric_size":         random.choice(["38px", "43px", "48px", "52px"]),
        "ticker_font_size":    random.choice(["26px", "28px", "30px", "32px"]),
        "prob_font_size":      random.choice(["36px", "42px", "48px"]),
        "reason_gap":          random.choice(["12px", "14px", "18px", "22px"]),
        # ── Structural visual treatments (dramatically change the look) ──────────
        "header_treatment":    random.choice(["plain", "hero", "hero", "boxed", "stripe", "badge"]),
        "panel_treatment":     random.choice(["border", "glow", "glow", "accent-left", "flat", "bold", "card"]),
        "bg_pattern":          random.choice(["none", "grid", "dots", "diagonal", "scanlines", "hex", "crosshatch"]),
        "losers_treatment":    random.choice(["plain", "colored", "colored", "striped", "numbered"]),
        "section_style":       random.choice(["plain", "flanked", "dashed", "heavy"]),
    }


def _highlight_headline(text, style):
    words = _esc(text).split()
    if len(words) < 2:
        return _esc(text)
    highlighted = " ".join(words[-2:])
    leading = " ".join(words[:-2])
    return f'{leading} <span class="accent">{highlighted}</span>'


def get_visual_style_names():
    """Return all available visual style names (pass as 'visual_style' in the data payload)."""
    return [s["name"] for s in VISUAL_STYLES]


# ── Fonts the LLM can pick from (all on Google Fonts) ────────────────────────
_FONT_OPTIONS = [
    "Inter", "JetBrains Mono", "Outfit", "Space Grotesk", "Exo 2",
    "Nunito", "Cormorant Garamond", "Playfair Display", "Share Tech Mono",
    "Barlow Condensed", "Raleway", "Josefin Sans", "Oswald", "IBM Plex Sans",
    "DM Sans", "Plus Jakarta Sans", "Roboto Mono", "Poppins", "Montserrat",
    "Bebas Neue", "Syne", "Chakra Petch", "Anton", "Orbitron",
]

# ── Layout classes that exist in the CSS ─────────────────────────────────────
_KNOWN_LAYOUTS = [
    "layout-classic",     # 3-column market cards, centred headline, full sections
    "layout-terminal",    # Left-aligned, wide first card, vertical losers list
    "layout-report",      # Editorial left-aligned, single-col summary
    "layout-blueprint",   # Horizontal card rows with grid-line accents
    "layout-neon",        # Classic structure with neon glow text-shadows
    "layout-aurora",      # Frosted-glass panels, heavily rounded corners
    "layout-magazine",    # Bold ruled headline, 2-col main grid, editorial
    "layout-midnight",    # Luxury serif, left-rule headline, borderless panels
    "layout-minimal",     # Maximum whitespace, type-driven, no panel fills
    # ── 6 New Layouts ───────────────────────────────────────────────────────
    "layout-ticker",      # Compact horizontal ticker-tape market cards
    "layout-split",       # Compressed top section, full-width main content
    "layout-dashboard",   # Equal-height data-dense grid panels
    "layout-editorial",   # Huge headline, editorial magazine with italic summary
    "layout-card-stack",  # Stacked flex market cards, content beside
    "layout-cinematic",   # Compressed header, wide-screen dramatic bottom half
]

_HEX_RE   = re.compile(r'^#[0-9a-fA-F]{3,8}$')
_RGB_RE   = re.compile(r'^rgba?\(')
_GRAD_RE  = re.compile(r'^(linear|radial)-gradient\(')
_SHAPE_RE = re.compile(r'^\d+(\.\d+)?(px|rem|em|%)$|^0$')


def _hex_to_rgba(hex_color: str, opacity: float) -> str:
    """Convert a #RRGGBB hex string to rgba(R,G,B,opacity)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{opacity})"


def _validate_and_repair_style(raw: dict) -> dict:
    """
    Validate the LLM-generated style dict and repair any invalid fields.
    Raises ValueError if the accent color (the single critical field) is broken.
    All other invalid fields fall back to safe defaults.
    """
    s = dict(raw)

    # accent ── critical; must be a valid CSS color
    accent = str(s.get("accent", "")).strip()
    if not (_HEX_RE.match(accent) or _RGB_RE.match(accent)):
        raise ValueError(f"LLM returned an invalid accent color: {accent!r}")

    # accent_soft ── derive from accent if missing/broken
    accent_soft = str(s.get("accent_soft", "")).strip()
    if not _RGB_RE.match(accent_soft):
        opacity = 0.20
        if _HEX_RE.match(accent):
            s["accent_soft"] = _hex_to_rgba(accent, opacity)
        else:
            s["accent_soft"] = f"rgba(255,255,255,{opacity})"

    # panel ── card background; must be a gradient or solid rgba
    panel = str(s.get("panel", "")).strip()
    if not (_GRAD_RE.match(panel) or _RGB_RE.match(panel)):
        s["panel"] = "linear-gradient(180deg, rgba(20,20,20,.52), rgba(4,4,4,.88))"

    # bg ── full poster background; anything non-empty is accepted
    if not str(s.get("bg", "")).strip():
        s["bg"] = "linear-gradient(180deg, #050505, #000000)"

    # layout ── must be one of the known CSS classes
    if s.get("layout") not in _KNOWN_LAYOUTS:
        s["layout"] = "layout-classic"

    # title_align
    if s.get("title_align") not in ("left", "center"):
        s["title_align"] = "center"

    # card_shape (border-radius)
    card_shape = str(s.get("card_shape", "")).strip()
    if not _SHAPE_RE.match(card_shape):
        s["card_shape"] = "8px"

    # font ── any non-empty string; construct the Google Fonts URL ourselves
    font = str(s.get("font", "")).strip() or "Inter"
    s["font"] = font
    font_slug = font.replace(" ", "+")
    s["font_url"] = (
        f"https://fonts.googleapis.com/css2?family={font_slug}"
        f":wght@400;700;900&display=swap"
    )

    s["name"] = "llm-generated"
    return s


async def generate_visual_style_for_market(data: dict, openai_client) -> dict:
    """
    Have the LLM invent a completely fresh visual style suited to today's
    market story — accent color, gradients, typography, and layout from scratch.

    Returns a fully-resolved style dict compatible with build_market_image_html().
    All CSS values are validated; the accent color is the only hard requirement.
    Any other invalid field is repaired to a safe default.
    Falls back to a random preset if the LLM is unavailable or produces a
    critically broken response.
    """
    if not openai_client:
        logger.warning("generate_visual_style_for_market: no OpenAI client, using preset fallback.")
        return {**random.choice(VISUAL_STYLES), "_rationale": "fallback"}

    # ── Market context ────────────────────────────────────────────────────────
    headline  = data.get("headline", "")
    theme     = data.get("theme", "")      # "red" | "green" | "mixed" etc.
    summary   = data.get("summary_text", "")
    date_line = data.get("date_line", "")
    bottom    = data.get("bottom_line", "")
    losers    = data.get("losers") or []
    losers_str = ", ".join(
        f"{l.get('ticker')} {l.get('value')}" for l in losers[:5]
    ) or "none"
    cards     = data.get("market_cards") or []
    cards_str = "  ".join(
        f"{c.get('label')} {c.get('value')}" for c in cards[:3]
    ) or "none"

    # ── Few-shot format examples pulled from existing presets ─────────────────
    _EXAMPLE_KEYS = ["classic", "terminal", "aurora", "volcanic", "midnight"]
    _EXAMPLE_STYLES = [
        s for s in VISUAL_STYLES if s["name"] in _EXAMPLE_KEYS
    ]
    _EXAMPLE_FIELDS = ["accent", "accent_soft", "panel", "bg",
                       "layout", "title_align", "card_shape", "font"]

    def _fmt_example(s):
        return json.dumps(
            {k: s[k] for k in _EXAMPLE_FIELDS if k in s}, indent=2
        )

    examples_block = "\n\n".join(
        f"// Preset '{s['name']}' (reference only — do NOT copy):\n{_fmt_example(s)}"
        for s in _EXAMPLE_STYLES
    )

    layouts_str  = "\n".join(f"  - {l}" for l in _KNOWN_LAYOUTS)
    fonts_str    = ", ".join(_FONT_OPTIONS)

    prompt = f"""You are the visual design director for a premium financial media brand.
Design a UNIQUE, visually striking style for today's market summary image.
Every post must look different — never reuse yesterday's look.

MARKET CONTEXT
--------------
Date       : {date_line}
Headline   : {headline}
Theme      : {theme}
Indices    : {cards_str}
Top movers : {losers_str}
Summary    : {summary}
Bottom line: {bottom}

DESIGN RULES
------------
1. Dark background only (near-black base). No light themes.
2. Pick a vivid, purposeful accent color that fits the story's emotion.
   Do NOT default to plain red or green — explore the full color space.
3. Gradients need depth: 2-3 stops with varying opacity.
4. Typography must match the mood:
   - Panic/urgent → condensed or mono
   - Premium/macro → serif
   - Tech/growth  → geometric sans
   - Minimal/quiet → clean sans
5. The layout should complement the data density and headline style.
6. Make it look like it belongs on a premium financial news channel.

AVAILABLE LAYOUTS (pick exactly one):
{layouts_str}

RECOMMENDED FONTS (or any Google Font):
{fonts_str}

FORMAT REFERENCE (existing presets — do NOT copy, use as CSS format guide only):
{examples_block}

Return ONLY this JSON — no markdown, no extra keys:
{{
  "accent": "<vivid hex color matching the emotional tone>",
  "accent_soft": "<rgba(...) version at .15-.25 opacity>",
  "panel": "<CSS gradient for card panel backgrounds — dark>",
  "bg": "<CSS gradient(s) for the full poster background — can chain multiple with comma>",
  "layout": "<one layout from the list>",
  "title_align": "<left or center>",
  "card_shape": "<border-radius e.g. 0px, 4px, 16px>",
  "font": "<Google Font name>",
  "rationale": "<one sentence: why this specific design fits today's story>"
}}"""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a visual design director for a financial media brand. "
                        "Respond only with the JSON object requested. No markdown fences, no extra text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.92,  # high creativity; every day should look different
            max_tokens=420,
            response_format={"type": "json_object"},
        )
        raw      = response.choices[0].message.content.strip()
        parsed   = json.loads(raw)
        rationale = str(parsed.pop("rationale", "")).strip()

        style = _validate_and_repair_style(parsed)
        style["_rationale"] = rationale

        logger.info(
            "LLM generated style for '%s' | layout=%-20s accent=%s | %s",
            headline, style["layout"], style["accent"], rationale,
        )
        return style

    except Exception as exc:
        logger.warning(
            "generate_visual_style_for_market failed (%s) — using preset fallback.", exc
        )
        return {**random.choice(VISUAL_STYLES), "_rationale": "fallback"}


def parse_market_data(raw_data=None):
    """
    Normalise the incoming data payload.
    - raw_data=None  -> random sample variant with today's date
    - raw_data=dict  -> merged on top of SAMPLE_MARKET_DATA defaults
    - raw_data=str   -> parsed as JSON then merged
    Include "visual_style": "<name>" to pin a theme.
    Available: classic, terminal, amber, blueprint, neon, glacier,
    midnight, crimson, matrix, volcanic, aurora, rose, solar, cobalt, emerald, onyx.
    """
    if not raw_data:
        data = deepcopy(random.choice(SAMPLE_MARKET_DATA_VARIANTS))
        data["date_line"] = datetime.now(timezone.utc).strftime("%B %d, %Y").upper() + " - MARKET SUMMARY"
        # visual_style intentionally NOT set here — caller should use
        # choose_visual_style_for_market() or pass it explicitly in the payload.
        return data
    if isinstance(raw_data, dict):
        data = deepcopy(SAMPLE_MARKET_DATA)
        data.update(raw_data)
        return data
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Market image data must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Market image data must be a JSON object")
    data = deepcopy(SAMPLE_MARKET_DATA)
    data.update(parsed)
    return data


def sample_market_data_json():
    return json.dumps(SAMPLE_MARKET_DATA, indent=2)


def build_market_image_html(data):
    style       = _visual_style(data)
    accent      = style["accent"]
    accent_soft = style["accent_soft"]
    panel_bg    = style["panel"]
    bg          = style["bg"]
    layout      = style["layout"]
    title_align = style["title_align"]
    card_shape  = style["card_shape"]
    font        = style.get("font", "Arial")
    font_url    = style.get("font_url", "")
    font_link   = f'<link href="{font_url}" rel="stylesheet">' if font_url else ""
    # ── Per-render micro-variations: makes every image visually unique ────────
    micro              = _random_micro_variations()
    spark_stroke       = micro["sparkline_stroke"]
    headline_spacing   = micro["headline_spacing"]
    headline_weight    = micro["headline_weight"]
    section_title_extra = _section_title_css(micro["section_title_style"], accent)
    footer_justify     = micro["footer_justify"]
    loser_radius       = micro["loser_radius"]
    icon_radius        = micro["icon_radius"]
    grid_gap           = micro["grid_gap"]
    card_height        = micro["card_height"]
    subhead_style      = micro["subhead_style"]
    metric_size        = micro["metric_size"]
    ticker_size        = micro["ticker_font_size"]
    prob_font_size     = micro["prob_font_size"]
    reason_gap         = micro["reason_gap"]
    # ── Structural treatment classes ───────────────────────────────────
    header_cls  = f"header-{micro['header_treatment']}"
    panel_cls   = f"panels-{micro['panel_treatment']}"
    losers_cls  = f"losers-{micro['losers_treatment']}"
    section_cls = f"section-{micro['section_style']}"
    poster_classes = f"{layout} {header_cls} {panel_cls} {losers_cls} {section_cls}"
    # ── Background pattern layer ───────────────────────────────────────
    bg_pattern_layer = _build_bg_pattern(micro["bg_pattern"], accent_soft)
    bg_size_extra    = ", 24px 24px" if micro["bg_pattern"] == "dots" else ""
    bg_with_pattern  = f"{bg_pattern_layer}, {bg}" if bg_pattern_layer else bg
    market_cards = data.get("market_cards") or []
    cards_html = "".join(
        f"""
        <section class="panel market-card">
          <h2>{_esc(card.get("label"))}</h2>
          <div class="metric accent">{_esc(card.get("value"))}</div>
          <div class="note">{_esc(card.get("note"))}</div>
          {_sparkline(card.get("chart"), accent, uid=f"c{i}", stroke_width=spark_stroke)}
        </section>
        """
        for i, card in enumerate(market_cards[:3])
    )
    losers = data.get("losers") or []
    losers_html = "".join(
        f"""
        <section class="loser">
          <div class="ticker">{_esc(item.get("ticker"))}</div>
          <div class="company">{_esc(item.get("name"))}</div>
          <div class="loss accent">{_esc(item.get("value"))}</div>
        </section>
        """
        for item in losers[:5]
    )
    reasons_html = "".join(
        f"""
        <div class="reason">
          <div class="reason-icon">{_reason_icon(reason.get("icon"))}</div>
          <div>
            <h4>{_esc(reason.get("title"))}</h4>
            <p>{_esc(reason.get("body"))}</p>
          </div>
        </div>
        """
        for reason in (data.get("reasons") or [])[:3]
    )
    scenarios_html = "".join(
        f"""
        <section class="scenario {scenario.get("color", "red")}">
          <div>
            <h4>{_esc(scenario.get("title"))}</h4>
            <h5>{_esc(scenario.get("subtitle"))}</h5>
            <ul>{_list_items(scenario.get("points"))}</ul>
          </div>
          <div class="trend {scenario.get("color", "red")}"></div>
        </section>
        """
        for scenario in (data.get("scenarios") or [])[:3]
    )
    probability_html = "".join(
        f"""
        <div class="prob {prob.get("color", "red")}">
          <strong>{_esc(prob.get("value"))}</strong>
          <span>{_esc(prob.get("label"))}</span>
        </div>
        """
        for prob in (data.get("probabilities") or [])[:3]
    )
    jobs         = data.get("jobs") or {}
    webaddress   = data.get("webaddress") or MARKET_IMAGE_COMPANY_WEBADDRESS
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    headline     = _highlight_headline(data.get("headline"), style)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  {font_link}
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; width: 1080px; height: 1620px; overflow: hidden; background: #050202; color: #f5f5f5; font-family: '{font}', Arial, Helvetica, sans-serif; }}
    .poster {{ width: 1385px; height: 2077px; zoom: .78; padding: 34px 30px 24px; background: {bg_with_pattern}; background-size: auto{bg_size_extra}; }}
    .headline {{ text-align: {title_align}; font-size: 76px; line-height: .92; font-weight: {headline_weight}; letter-spacing: {headline_spacing}; text-transform: uppercase; }}
    .accent {{ color: {accent}; }}
    .subhead {{ margin-top: 12px; text-align: {title_align}; color: #d9d9d9; font-size: 31px; font-weight: 800; letter-spacing: 2px; font-style: {subhead_style}; }}
    .grid-top {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: {grid_gap}; margin-top: 18px; }}
    .panel {{ border: 1.5px solid {accent}; border-radius: {card_shape}; background: {panel_bg}; box-shadow: inset 0 0 24px {accent_soft}; }}
    .market-card {{ height: {card_height}; padding: 18px 14px; position: relative; overflow: hidden; }}
    .market-card h2 {{ margin: 0; text-align: center; font-size: 31px; line-height: 1.05; font-weight: 900; }}
    .metric {{ text-align: center; font-size: {metric_size}; font-weight: 900; margin-top: 8px; }}
    .red {{ color: #e11d2e; }} .green {{ color: #2dd46f; }} .amber {{ color: #f0b93d; }}
    .note {{ text-align: center; color: #ccc; min-height: 22px; font-size: 16px; }}
    .sparkline {{ position: absolute; left: 16px; right: 16px; bottom: 14px; width: calc(100% - 32px); height: 78px; filter: drop-shadow(0 0 7px {accent_soft}); }}
    .summary-row {{ display: grid; grid-template-columns: 2fr 1.1fr; gap: {grid_gap}; margin-top: {grid_gap}; }}
    .summary {{ min-height: 102px; padding: 18px; font-size: 24px; line-height: 1.35; }}
    .jobs {{ padding: 13px; text-align: center; }}
    .jobs h3 {{ margin: 0; font-size: 22px; }} .jobs .value {{ color: {accent}; font-size: 39px; font-weight: 900; }} .jobs .note {{ font-size: 20px; }}
    .losers-box {{ margin-top: {grid_gap}; padding: 14px 0 0; }}
    .section-title {{ text-align: center; font-size: 31px; font-weight: 900; letter-spacing: 1px; {section_title_extra} }}
    .losers-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); margin-top: 10px; border-top: 1px solid {accent}; }}
    .loser {{ min-height: 160px; padding: 18px 9px; border-right: 1px solid {accent}; text-align: center; border-radius: {loser_radius}; overflow: hidden; }}
    .loser:last-child {{ border-right: none; }}
    .ticker {{ font-size: {ticker_size}; font-weight: 900; color: #f1f1f1; min-height: 38px; }}
    .company {{ margin-top: 16px; font-size: 22px; line-height: 1.1; min-height: 55px; }}
    .loss {{ font-size: 38px; font-weight: 900; margin-top: 10px; }}
    .main-grid {{ display: grid; grid-template-columns: .95fr 1.4fr; gap: {grid_gap}; margin-top: 16px; }}
    .reasons, .forecast {{ padding: 14px 16px; min-height: 510px; }}
    .reason {{ display: grid; grid-template-columns: 70px 1fr; gap: {reason_gap}; margin-top: 18px; align-items: start; }}
    .reason-icon {{ width: 70px; height: 70px; border: 1px solid {accent}; border-radius: {icon_radius}; display: grid; place-items: center; color: {accent}; font-size: 38px; font-weight: 900; background: {accent_soft}; }}
    .reason h4 {{ margin: 0 0 4px; font-size: 23px; }} .reason p {{ margin: 0; font-size: 20px; line-height: 1.28; color: #ddd; }}
    .scenario {{ min-height: 140px; display: grid; grid-template-columns: 1fr 160px; gap: 12px; margin-top: 10px; border-radius: 8px; padding: 13px 18px; border: 1px solid rgba(255,255,255,.08); }}
    .scenario.green {{ background: rgba(0,80,35,.28); }} .scenario.amber {{ background: rgba(120,80,0,.22); }} .scenario.red {{ background: rgba(100,0,0,.25); }}
    .scenario h4 {{ margin: 0; font-size: 21px; }} .scenario h5 {{ margin: 5px 0 7px; font-size: 21px; }} .scenario ul {{ margin: 0; padding-left: 22px; font-size: 18px; line-height: 1.35; }}
    .trend {{ align-self: center; height: 80px; }}
    .trend.green {{ background: linear-gradient(135deg, transparent 8%, #2dd46f 9% 14%, transparent 15% 29%, #2dd46f 30% 35%, transparent 36% 49%, #2dd46f 50% 55%, transparent 56%); transform: rotate(-12deg); }}
    .trend.amber {{ background: linear-gradient(135deg, transparent 12%, #f0b93d 13% 18%, transparent 19% 36%, #f0b93d 37% 42%, transparent 43% 64%, #f0b93d 65% 70%, transparent 71%); }}
    .trend.red {{ background: linear-gradient(45deg, transparent 12%, #e11d2e 13% 18%, transparent 19% 36%, #e11d2e 37% 42%, transparent 43% 64%, #e11d2e 65% 70%, transparent 71%); transform: rotate(22deg); }}
    .bottom-grid {{ display: grid; grid-template-columns: 1.35fr 1fr; gap: {grid_gap}; margin-top: 14px; }}
    .prob-box, .bottom-line {{ padding: 13px 18px; min-height: 150px; }}
    .prob-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid {accent}; margin-top: 10px; }}
    .prob {{ text-align: center; padding: 12px 5px; border-right: 1px solid {accent}; }} .prob:last-child {{ border-right: 0; }}
    .prob strong {{ display: block; font-size: {prob_font_size}; }} .prob span {{ display: block; font-size: 15px; font-weight: 800; }}
    .bottom-line p {{ font-size: 22px; line-height: 1.35; margin: 18px 0 0; color: #ddd; }}
    .footer {{ margin-top: 14px; display: flex; justify-content: {footer_justify}; align-items: flex-end; gap: 20px; color: #bdbdbd; font-size: 16px; }}
    .brand {{ color: #f0f0f0; font-weight: 900; font-size: 25px; white-space: nowrap; }}
    /* ── layout-terminal ── */
    .layout-terminal .headline {{ border-left: 12px solid {accent}; padding-left: 24px; }}
    .layout-terminal .grid-top {{ grid-template-columns: 1.2fr .9fr .9fr; }}
    .layout-terminal .losers-grid {{ grid-template-columns: 1fr; }}
    .layout-terminal .losers-box {{ float: right; width: 37%; margin-left: 12px; min-height: 470px; }}
    .layout-terminal .loser {{ min-height: 80px; display: grid; grid-template-columns: 90px 1fr 95px; align-items: center; text-align: left; border-right: 0; border-bottom: 1px solid {accent}; padding: 10px 14px; }}
    .layout-terminal .company {{ margin-top: 0; min-height: auto; font-size: 18px; }}
    .layout-terminal .loss {{ margin-top: 0; text-align: right; font-size: 29px; }}
    .layout-terminal .main-grid {{ grid-template-columns: 1fr; }}
    .layout-terminal .reasons, .layout-terminal .forecast {{ min-height: 385px; }}
    /* ── layout-report ── */
    .layout-report .headline {{ max-width: 760px; }}
    .layout-report .summary-row {{ grid-template-columns: 1fr; }}
    .layout-report .jobs {{ display: grid; grid-template-columns: 1fr 1fr 1fr; align-items: center; text-align: left; }}
    .layout-report .losers-grid {{ grid-template-columns: repeat(5, 1fr); }}
    .layout-report .main-grid {{ grid-template-columns: 1.25fr 1fr; }}
    .layout-report .forecast {{ order: -1; }}
    /* ── layout-blueprint ── */
    .layout-blueprint.poster {{ padding: 42px; }}
    .layout-blueprint .grid-top {{ grid-template-columns: 1fr; }}
    .layout-blueprint .market-card {{ height: 145px; display: grid; grid-template-columns: 1fr 160px 280px; align-items: center; }}
    .layout-blueprint .sparkline {{ position: static; width: 280px; height: 92px; }}
    .layout-blueprint .summary-row {{ grid-template-columns: 1fr; }}
    .layout-blueprint .main-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-blueprint .reasons, .layout-blueprint .forecast {{ min-height: 490px; }}
    /* ── layout-neon: headlines and panels emit cyan/purple light ── */
    .layout-neon .headline {{ text-shadow: 0 0 60px {accent}, 0 0 120px {accent_soft}; letter-spacing: 4px; }}
    .layout-neon .panel {{ box-shadow: 0 0 28px {accent_soft}, inset 0 0 28px {accent_soft}; }}
    .layout-neon .metric {{ text-shadow: 0 0 22px {accent}; }}
    .layout-neon .accent {{ text-shadow: 0 0 18px {accent}; }}
    .layout-neon .section-title {{ text-shadow: 0 0 14px {accent}; letter-spacing: 3px; }}
    .layout-neon .reason-icon {{ box-shadow: 0 0 16px {accent_soft}; }}
    .layout-neon .sparkline {{ filter: drop-shadow(0 0 10px {accent}); }}
    /* ── layout-aurora: frosted-glass glassmorphism panels ── */
    .layout-aurora.poster {{ padding: 40px 36px; }}
    .layout-aurora .panel {{ backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); background: rgba(255,255,255,.045) !important; border-color: rgba(255,255,255,.16); border-radius: 24px !important; }}
    .layout-aurora .grid-top {{ gap: 16px; }}
    .layout-aurora .market-card {{ height: 228px; padding: 22px 18px; }}
    .layout-aurora .market-card h2 {{ font-size: 29px; }}
    .layout-aurora .headline {{ text-shadow: 0 0 80px {accent_soft}; letter-spacing: 3px; }}
    .layout-aurora .subhead {{ color: {accent}; opacity: .85; }}
    .layout-aurora .scenario {{ background: rgba(255,255,255,.04) !important; border-color: rgba(255,255,255,.12); border-radius: 16px; }}
    .layout-aurora .reason-icon {{ border-radius: 16px; }}
    /* ── layout-magazine: bold editorial with ruled headline ── */
    .layout-magazine .headline {{ font-size: 86px; line-height: .87; padding-bottom: 14px; border-bottom: 4px solid {accent}; letter-spacing: 1px; }}
    .layout-magazine .subhead {{ font-size: 25px; letter-spacing: 6px; margin-top: 10px; color: {accent}; opacity: .8; }}
    .layout-magazine .grid-top {{ gap: 10px; margin-top: 14px; }}
    .layout-magazine .summary-row {{ grid-template-columns: 1fr; }}
    .layout-magazine .main-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-magazine .section-title {{ font-size: 28px; letter-spacing: 3px; border-bottom: 1px solid {accent}; padding-bottom: 8px; text-align: left; }}
    /* ── layout-midnight: luxury serif with left-rule headline ── */
    .layout-midnight .headline {{ font-size: 70px; letter-spacing: 5px; border-left: 6px solid {accent}; padding-left: 28px; line-height: .94; }}
    .layout-midnight .subhead {{ letter-spacing: 9px; font-size: 21px; color: {accent}; opacity: .78; margin-top: 16px; padding-left: 34px; }}
    .layout-midnight .panel {{ border-width: 1px 0 0 0; border-style: solid; border-radius: 0 !important; background: transparent !important; box-shadow: none !important; }}
    .layout-midnight .market-card h2 {{ text-align: left; font-size: 26px; letter-spacing: 4px; font-style: italic; }}
    .layout-midnight .metric {{ text-align: left; font-size: 48px; }}
    .layout-midnight .note {{ text-align: left; }}
    .layout-midnight .summary {{ font-size: 22px; font-style: italic; line-height: 1.5; }}
    .layout-midnight .section-title {{ letter-spacing: 6px; font-size: 20px; text-align: left; border-bottom: 1px solid {accent}; padding-bottom: 8px; }}
    .layout-midnight .reason-icon {{ border-radius: 0; }}
    .layout-midnight .sparkline {{ filter: drop-shadow(0 0 6px {accent_soft}); }}
    /* ── layout-minimal: borders only, maximum whitespace, type-driven ── */
    .layout-minimal .panel {{ background: transparent !important; border-color: rgba(255,255,255,.18); border-radius: 0 !important; box-shadow: none !important; }}
    .layout-minimal .headline {{ font-size: 64px; letter-spacing: -2px; font-weight: 800; }}
    .layout-minimal .subhead {{ font-size: 18px; letter-spacing: 8px; color: rgba(255,255,255,.5); font-weight: 400; }}
    .layout-minimal .metric {{ font-size: 50px; }}
    .layout-minimal .market-card {{ height: 195px; }}
    .layout-minimal .summary {{ font-size: 22px; }}
    .layout-minimal .section-title {{ font-size: 14px; letter-spacing: 5px; color: rgba(255,255,255,.45); font-weight: 400; text-transform: uppercase; border-bottom: 1px solid rgba(255,255,255,.12); padding-bottom: 6px; }}
    .layout-minimal .reason-icon {{ border: 1px solid rgba(255,255,255,.18); background: transparent; }}
    .layout-minimal .scenario {{ border-color: rgba(255,255,255,.1); border-radius: 0; }}
    .layout-minimal .scenario.green, .layout-minimal .scenario.amber, .layout-minimal .scenario.red {{ background: rgba(255,255,255,.03); }}
    .layout-minimal .sparkline {{ filter: none; }}
    .layout-minimal .ticker {{ color: {accent}; }}
    /* ── layout-ticker: compact horizontal ticker-tape cards ── */
    .layout-ticker .grid-top {{ grid-template-columns: 1fr; gap: 8px; margin-top: 12px; }}
    .layout-ticker .market-card {{ height: 112px; display: grid; grid-template-columns: 210px 155px 1fr; align-items: center; padding: 12px 18px; }}
    .layout-ticker .market-card h2 {{ text-align: left; font-size: 24px; margin: 0; letter-spacing: 2px; }}
    .layout-ticker .metric {{ text-align: left; font-size: 38px; margin-top: 0; }}
    .layout-ticker .note {{ text-align: left; margin-top: 0; }}
    .layout-ticker .sparkline {{ position: static; width: 100%; height: 72px; }}
    .layout-ticker .summary-row {{ grid-template-columns: 1.6fr 1fr; }}
    .layout-ticker .main-grid {{ grid-template-columns: 1fr 1fr; }}
    /* ── layout-split: compressed top section, full-width main content ── */
    .layout-split .grid-top {{ grid-template-columns: 1fr; gap: 8px; }}
    .layout-split .market-card {{ height: 126px; display: grid; grid-template-columns: 1fr 130px 210px; align-items: center; padding: 12px 16px; }}
    .layout-split .market-card h2 {{ font-size: 22px; text-align: left; margin: 0; }}
    .layout-split .metric {{ font-size: 36px; margin-top: 0; text-align: left; }}
    .layout-split .note {{ text-align: left; margin-top: 0; }}
    .layout-split .sparkline {{ position: static; width: 210px; height: 80px; }}
    .layout-split .main-grid {{ grid-template-columns: 1fr; }}
    .layout-split .reasons, .layout-split .forecast {{ min-height: 360px; }}
    /* ── layout-dashboard: equal-height data-dense grid panels ── */
    .layout-dashboard .grid-top {{ grid-template-columns: repeat(3,1fr); gap: 10px; }}
    .layout-dashboard .market-card {{ height: 176px; }}
    .layout-dashboard .main-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-dashboard .reasons, .layout-dashboard .forecast {{ min-height: 420px; }}
    .layout-dashboard .bottom-grid {{ grid-template-columns: 1.35fr 1fr; }}
    .layout-dashboard .panel {{ border-radius: 4px !important; }}
    .layout-dashboard .summary-row {{ grid-template-columns: 1.5fr 1fr; }}
    /* ── layout-editorial: huge headline, editorial italic summary ── */
    .layout-editorial .headline {{ font-size: 98px; line-height: .85; letter-spacing: -3px; }}
    .layout-editorial .subhead {{ font-size: 20px; letter-spacing: 7px; opacity: .7; }}
    .layout-editorial .grid-top {{ margin-top: 10px; }}
    .layout-editorial .market-card {{ height: 158px; }}
    .layout-editorial .market-card h2 {{ font-size: 24px; }}
    .layout-editorial .metric {{ font-size: 34px; }}
    .layout-editorial .sparkline {{ height: 58px; }}
    .layout-editorial .summary {{ font-size: 26px; line-height: 1.42; font-style: italic; }}
    .layout-editorial .main-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-editorial .section-title {{ font-size: 13px; letter-spacing: 5px; color: rgba(255,255,255,.45); font-weight: 400; border-bottom: 1px solid rgba(255,255,255,.14); padding-bottom: 6px; }}
    /* ── layout-card-stack: stacked flex market cards ── */
    .layout-card-stack .grid-top {{ grid-template-columns: 1fr; gap: 8px; }}
    .layout-card-stack .market-card {{ height: 96px; padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; gap: 14px; overflow: hidden; }}
    .layout-card-stack .market-card h2 {{ font-size: 22px; margin: 0; min-width: 180px; text-align: left; }}
    .layout-card-stack .metric {{ font-size: 36px; margin-top: 0; text-align: left; }}
    .layout-card-stack .note {{ text-align: left; min-height: auto; margin-top: 0; }}
    .layout-card-stack .sparkline {{ position: static; width: 220px; height: 66px; flex-shrink: 0; }}
    .layout-card-stack .main-grid {{ grid-template-columns: 1fr 1fr; }}
    /* ── layout-cinematic: compressed header, wide-screen dramatic feel ── */
    .layout-cinematic .headline {{ font-size: 68px; letter-spacing: 4px; line-height: .92; }}
    .layout-cinematic .subhead {{ font-size: 18px; letter-spacing: 9px; opacity: .65; margin-top: 14px; }}
    .layout-cinematic .grid-top {{ margin-top: 10px; }}
    .layout-cinematic .market-card {{ height: 185px; }}
    .layout-cinematic .summary-row {{ grid-template-columns: 1fr; }}
    .layout-cinematic .jobs {{ display: grid; grid-template-columns: 1fr 1fr 1fr; text-align: left; align-items: center; padding: 16px 18px; }}
    .layout-cinematic .main-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-cinematic .bottom-grid {{ grid-template-columns: 1fr 1fr; }}
    .layout-cinematic .section-title {{ letter-spacing: 4px; }}
    /* ══ STRUCTURAL TREATMENT CSS ══════════════════════════════════════════════ */
    /* ── header-hero: full-width accent band behind headline ── */
    .header-hero .headline-wrap {{ background: {accent}; margin: -34px -30px 0; padding: 26px 30px 22px; }}
    .header-hero .headline {{ color: #000 !important; }}
    .header-hero .accent {{ color: rgba(0,0,0,.55) !important; }}
    .header-hero .subhead {{ color: rgba(0,0,0,.6); margin-top: 0; padding-bottom: 8px; font-style: normal; }}
    /* ── header-boxed: outlined accent box around headline ── */
    .header-boxed .headline {{ border: 3px solid {accent}; padding: 14px 22px; display: inline-block; }}
    .header-boxed .headline-wrap {{ text-align: {title_align}; }}
    /* ── header-stripe: thick accent top stripe ── */
    .header-stripe .headline-wrap {{ border-top: 8px solid {accent}; padding-top: 14px; }}
    /* ── header-badge: glowing label above headline ── */
    .header-badge .headline-wrap::before {{ content: '▶ MARKET BRIEF ◀'; display: block; font-size: 18px; letter-spacing: 5px; color: {accent}; margin-bottom: 10px; text-align: {title_align}; text-shadow: 0 0 20px {accent}; }}
    /* ── panels-glow: invisible borders, glowing box-shadow panels ── */
    .panels-glow .panel {{ border-color: transparent !important; box-shadow: 0 0 36px {accent_soft}, 0 4px 20px rgba(0,0,0,.55), inset 0 0 36px {accent_soft}; }}
    /* ── panels-accent-left: thick left accent bar only ── */
    .panels-accent-left .panel {{ border: none !important; border-left: 6px solid {accent} !important; border-radius: 0 !important; background: rgba(255,255,255,.025) !important; box-shadow: none; }}
    /* ── panels-flat: subtle flat panels with barely-there borders ── */
    .panels-flat .panel {{ background: rgba(255,255,255,.055) !important; border-color: rgba(255,255,255,.09); box-shadow: none; }}
    /* ── panels-bold: extra thick glowing border ── */
    .panels-bold .panel {{ border-width: 2.5px !important; box-shadow: inset 0 0 44px {accent_soft}, 0 0 22px {accent_soft}; }}
    /* ── panels-card: elevated card with drop shadow, no glow ── */
    .panels-card .panel {{ border-color: rgba(255,255,255,.07); background: rgba(255,255,255,.04) !important; box-shadow: 0 10px 40px rgba(0,0,0,.65), 0 2px 10px rgba(0,0,0,.4); }}
    /* ── losers-colored: every loser cell tinted with accent ── */
    .losers-colored .loser {{ background: {accent_soft}; }}
    .losers-colored .losers-grid {{ border-top: 2px solid {accent}; }}
    /* ── losers-striped: alternating row shading ── */
    .losers-striped .loser:nth-child(odd) {{ background: rgba(255,255,255,.04); }}
    /* ── losers-numbered: show rank number via counter ── */
    .losers-numbered .losers-grid {{ counter-reset: loser-rank; }}
    .losers-numbered .loser {{ counter-increment: loser-rank; }}
    .losers-numbered .loser::before {{ content: counter(loser-rank); display: block; font-size: 14px; letter-spacing: 2px; color: {accent}; font-weight: 900; margin-bottom: 4px; }}
    /* ── section-flanked: accent flanks around section titles ── */
    .section-flanked .section-title::before {{ content: '─── '; color: {accent}; }}
    .section-flanked .section-title::after {{ content: ' ───'; color: {accent}; }}
    /* ── section-dashed: dashed accent separator under section titles ── */
    .section-dashed .section-title {{ border-bottom: 2px dashed {accent}; padding-bottom: 8px; }}
    /* ── section-heavy: bold solid separator with accent background chip ── */
    .section-heavy .section-title {{ background: {accent_soft}; padding: 8px 16px; border-left: 5px solid {accent}; text-align: left; }}
  </style>
</head>
<body>
  <div class="poster {poster_classes}">
    <div class="headline-wrap">
      <div class="headline">{headline}</div>
      <div class="subhead">{_esc(data.get("date_line"))}</div>
    </div>
    <div class="grid-top">{cards_html}</div>
    <div class="summary-row">
      <section class="panel summary">{_esc(data.get("summary_text"))}</section>
      <section class="panel jobs"><h3>{_esc(jobs.get("label"))}</h3><div class="value">{_esc(jobs.get("value"))}</div><div class="note">{_esc(jobs.get("note"))}</div></section>
    </div>
    <section class="panel losers-box"><div class="section-title">{_esc(data.get("losers_title"))}</div><div class="losers-grid">{losers_html}</div></section>
    <div class="main-grid">
      <section class="panel reasons"><div class="section-title">WHY DID THEY FALL?</div>{reasons_html}</section>
      <section class="panel forecast"><div class="section-title">FORECAST FOR NEXT WEEK</div>{scenarios_html}</section>
    </div>
    <div class="bottom-grid">
      <section class="panel prob-box"><div class="section-title">MY CURRENT PROBABILITY</div><div class="prob-grid">{probability_html}</div></section>
      <section class="panel bottom-line"><div class="section-title">BOTTOM LINE</div><p>{_esc(data.get("bottom_line"))}</p></section>
    </div>
    <div class="footer">
      <span>{_esc(data.get("disclaimer"))} Market data as of {_esc(data.get("date_line") or generated_at)}.</span>
      <span class="brand">{_esc(webaddress or MARKET_IMAGE_BRAND_NAME)}</span>
    </div>
  </div>
</body>
</html>"""


async def render_market_image(data):
    html_content = build_market_image_html(data)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1080, "height": 1620}, device_scale_factor=1)
        await page.set_content(html_content, wait_until="networkidle")
        image = await page.screenshot(type="png", full_page=False)
        await browser.close()
    return image
