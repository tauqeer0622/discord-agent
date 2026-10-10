import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
try:
    from .config import config
    from .database import add_log
except (ImportError, ValueError):
    from config import config
    from database import add_log

logger = logging.getLogger("x_engine.s3_fetcher")

DEFAULT_FALLBACK_EVENTS = [
    {
        "time": "08:30",
        "country": "US",
        "currency": "USD",
        "event": "ADP Employment Change Weekly",
        "reason": "A key indicator of U.S. labor market strength.",
        "impact": "High"
    },
    {
        "time": "10:00",
        "country": "EU",
        "currency": "EUR",
        "event": "ECB Interest Rate Decision",
        "reason": "Markets will watch for signs of future policy direction from ECB.",
        "impact": "High"
    },
    {
        "time": "12:30",
        "country": "US",
        "currency": "USD",
        "event": "CPI m/m",
        "reason": "A major inflation gauge. Higher inflation may impact Fed rate hikes.",
        "impact": "High"
    },
    {
        "time": "14:00",
        "country": "GB",
        "currency": "GBP",
        "event": "BOE Governor Speaks",
        "reason": "Comments may influence GBP and UK gilt volatility.",
        "impact": "Medium"
    },
    {
        "time": "15:30",
        "country": "US",
        "currency": "USD",
        "event": "Core PPI m/m",
        "reason": "Measure of wholesale producer level inflation.",
        "impact": "Medium"
    },
    {
        "time": "17:00",
        "country": "JP",
        "currency": "JPY",
        "event": "Existing Home Sales",
        "reason": "Shows the health of the U.S. and global housing market.",
        "impact": "Medium"
    },
    {
        "time": "18:30",
        "country": "CA",
        "currency": "CAD",
        "event": "Crude Oil Inventories",
        "reason": "Tracks weekly drawdown in crude oil stockpiles.",
        "impact": "Medium"
    },
    {
        "time": "20:00",
        "country": "US",
        "currency": "USD",
        "event": "FOMC Member Speaks",
        "reason": "Guidance on macroeconomic policy and liquidity.",
        "impact": "High"
    },
    {
        "time": "14:00",
        "country": "CN",
        "currency": "CNY",
        "event": "PBOC Interest Rate Decision",
        "reason": "Signals regarding monetary liquidity and loan rates.",
        "impact": "Medium"
    },
    {
        "time": "16:00",
        "country": "US",
        "currency": "USD",
        "event": "Retail Sales m/m",
        "reason": "Consumer spending pulse driving economic trajectory.",
        "impact": "Medium"
    }
]

DEFAULT_FALLBACK_NEWS = {
    "date": "2026-10-03",
    "time": "12:00 UTC",
    "title": "GLOBAL MARKET INTELLIGENCE",
    "summary": "Bitcoin consolidates above key support as global markets digest rate sentiment and ETF inflows.",
    "sentiment": "Bullish",
    "sentiment_score": 68,
    "events": DEFAULT_FALLBACK_EVENTS,
    "headlines": [
        {
            "category": "CRYPTO",
            "title": "Bitcoin holds $65K as institutional ETF net inflows surge for 4th consecutive session.",
            "impact": "Bullish"
        },
        {
            "category": "MACRO",
            "title": "Fed rate cut expectations firm up ahead of upcoming inflation and employment data.",
            "impact": "Neutral"
        },
        {
            "category": "EQUITIES",
            "title": "Tech sector leads index momentum with semiconductor stocks driving broad market volume.",
            "impact": "Bullish"
        },
        {
            "category": "ON-CHAIN",
            "title": "Ethereum staking volume hits new quarterly milestone despite subdued mainnet gas fees.",
            "impact": "Neutral"
        }
    ],
    "key_stats": [
        {"label": "BTC / USD", "value": "$65,840", "change": "+2.4%"},
        {"label": "ETH / USD", "value": "$2,640", "change": "+1.8%"},
        {"label": "DXY Index", "value": "100.8", "change": "-0.3%"},
        {"label": "Fear & Greed", "value": "64 (Greed)", "change": "Greed"}
    ]
}

def fetch_s3_market_json() -> Dict[str, Any]:
    """
    Fetches the latest Market News & Headline JSON payload.
    Supports:
      1. AWS S3 via boto3 (using AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME)
      2. Public S3 URL via HTTPS (using S3_PUBLIC_URL)
      3. Graceful fallback data if S3 credentials are not yet configured.
    """
    # 1. Try AWS S3 via boto3 if bucket name is set
    bucket = getattr(config, "s3_bucket_name", "").strip() or os.getenv("S3_BUCKET_NAME", "").strip()
    key = getattr(config, "s3_json_key", "").strip() or os.getenv("S3_JSON_KEY", "latest_news.json").strip()
    aws_key = getattr(config, "aws_access_key_id", "").strip() or os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret = getattr(config, "aws_secret_access_key", "").strip() or os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    aws_region = getattr(config, "aws_region", "").strip() or os.getenv("AWS_REGION", "us-east-1").strip()

    if bucket and key:
        try:
            import boto3
            boto_kwargs = {"region_name": aws_region}
            if aws_key and aws_secret:
                boto_kwargs["aws_access_key_id"] = aws_key
                boto_kwargs["aws_secret_access_key"] = aws_secret

            s3_client = boto3.client("s3", **boto_kwargs)
            add_log("INFO", f"Fetching market news JSON from S3 bucket: {bucket}/{key}")
            response = s3_client.get_object(Bucket=bucket, Key=key)
            raw_body = response["Body"].read().decode("utf-8")
            data = json.loads(raw_body)
            add_log("INFO", f"Successfully loaded S3 JSON from s3://{bucket}/{key}")
            return normalize_market_json(data)
        except Exception as e:
            add_log("WARN", f"S3 fetch failed ({str(e)}). Checking public URL or fallback.")

    # 2. Try Public S3 URL if configured
    public_url = getattr(config, "s3_public_url", "").strip() or os.getenv("S3_PUBLIC_URL", "").strip()
    if public_url:
        try:
            import httpx
            add_log("INFO", f"Fetching market news JSON from public S3 URL: {public_url}")
            resp = httpx.get(public_url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                add_log("INFO", "Successfully loaded market news from public S3 URL.")
                return normalize_market_json(data)
        except Exception as e:
            add_log("WARN", f"Public S3 URL fetch failed: {str(e)}")

    # 3. Check for local json file drop in x_engine/data/market_news.json
    local_json_path = os.path.join(os.path.dirname(__file__), "data", "market_news.json")
    if os.path.exists(local_json_path):
        try:
            with open(local_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                add_log("INFO", "Loaded market news from local fallback file (market_news.json).")
                return normalize_market_json(data)
        except Exception:
            pass

    # 4. Fallback to default market news template
    add_log("INFO", "S3 credentials pending. Using default market news headline dataset.")
    return normalize_market_json(DEFAULT_FALLBACK_NEWS)

def normalize_market_json(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes arbitrary JSON shapes into standard format:
    - title, summary, date, sentiment
    - headlines: list of {category, title, impact}
    - key_stats: list of {label, value, change}
    """
    if not isinstance(data, dict):
        return DEFAULT_FALLBACK_NEWS

    result = {
        "title": data.get("title") or "GLOBAL MARKET INTELLIGENCE",
        "summary": data.get("summary") or "Key headlines, market sentiment, and price action overview.",
        "date": data.get("date") or datetime.utcnow().strftime("%Y-%m-%d"),
        "time": data.get("time") or datetime.utcnow().strftime("%H:%M UTC"),
        "sentiment": data.get("sentiment") or "Neutral",
        "sentiment_score": data.get("sentiment_score") or 50,
        "events": [],
        "headlines": [],
        "key_stats": []
    }

    # Extract economic calendar events from varied vendor schemas
    raw_events = (
        data.get("events")
        or data.get("key_events")
        or data.get("economic_calendar")
        or data.get("calendar")
        or data.get("market_events")
        or (data.get("data", {}).get("events") if isinstance(data.get("data"), dict) else [])
        or []
    )

    cc_currency_map = {
        "US": "USD", "EA": "EUR", "EU": "EUR", "JP": "JPY",
        "GB": "GBP", "UK": "GBP", "AU": "AUD", "CA": "CAD",
        "CN": "CNY", "CH": "CHF", "NZ": "NZD", "IN": "INR"
    }

    def infer_reason(ev_dict: Dict[str, Any]) -> str:
        if ev_dict.get("reason") or ev_dict.get("critical_reason") or ev_dict.get("description"):
            return str(ev_dict.get("reason") or ev_dict.get("critical_reason") or ev_dict.get("description"))
        cat = str(ev_dict.get("category") or "").lower()
        name = str(ev_dict.get("event_name") or ev_dict.get("event") or "").lower()
        prev = ev_dict.get("previous")
        fore = ev_dict.get("forecast")
        if "crude" in name or "gasoline" in name or "oil" in name or cat == "energy":
            return "Measures weekly supply changes influencing energy prices."
        elif "pmi" in name or cat == "trade":
            return "Leading economic indicator of manufacturing & service activity."
        elif "employment" in name or "job" in name or "unemployment" in name or cat == "employment":
            return "Critical gauge of labor market strength and economic momentum."
        elif "cpi" in name or "inflation" in name or "ppi" in name:
            return "Key inflation benchmark influencing central bank rate path."
        elif "rate" in name or "interest" in name:
            return "Monetary policy benchmark driving currency and bond yields."
        elif prev is not None and str(prev).strip():
            return f"Previous: {prev}" + (f" | Forecast: {fore}" if fore else "")
        return "Key indicator for economic direction and market sentiment."

    for item in raw_events:
        if isinstance(item, dict):
            raw_cc = str(item.get("country_code") or item.get("country") or item.get("cc") or "US").upper()
            display_cc = "EU" if raw_cc == "EA" else raw_cc
            raw_curr = str(item.get("currency") or item.get("curr") or cc_currency_map.get(raw_cc, raw_cc)).upper()

            raw_time = str(item.get("time") or item.get("event_time") or item.get("hour") or "12:00")
            clean_time = raw_time[:5] if len(raw_time) >= 5 else raw_time

            raw_pri = str(item.get("priority_level") or item.get("impact") or item.get("priority") or "Medium").capitalize()

            result["events"].append({
                "time": clean_time,
                "country": display_cc,
                "currency": raw_curr,
                "event": str(item.get("event_name") or item.get("event") or item.get("title") or item.get("name") or "Economic Event"),
                "reason": infer_reason(item),
                "impact": raw_pri
            })

    if not result["events"]:
        result["events"] = DEFAULT_FALLBACK_EVENTS

    # Extract headlines from varied keys
    raw_headlines = (
        data.get("headlines")
        or data.get("news")
        or data.get("articles")
        or data.get("items")
        or data.get("feed")
        or []
    )

    for item in raw_headlines[:4]:
        if isinstance(item, dict):
            result["headlines"].append({
                "category": item.get("category") or item.get("tag") or item.get("source") or "MARKET",
                "title": item.get("title") or item.get("headline") or item.get("text") or "Market update",
                "impact": item.get("impact") or item.get("sentiment") or "Neutral"
            })
        elif isinstance(item, str):
            result["headlines"].append({
                "category": "MARKET",
                "title": item,
                "impact": "Neutral"
            })

    # If no headlines found, use defaults
    if not result["headlines"]:
        result["headlines"] = DEFAULT_FALLBACK_NEWS["headlines"]

    # Extract stats from varied keys
    raw_stats = data.get("key_stats") or data.get("stats") or data.get("prices") or data.get("tickers") or []
    for s in raw_stats[:4]:
        if isinstance(s, dict):
            result["key_stats"].append({
                "label": s.get("label") or s.get("symbol") or s.get("ticker") or s.get("name") or "Asset",
                "value": s.get("value") or s.get("price") or "$0.00",
                "change": s.get("change") or s.get("delta") or "+0.0%"
            })

    if not result["key_stats"]:
        result["key_stats"] = DEFAULT_FALLBACK_NEWS["key_stats"]

    return result
