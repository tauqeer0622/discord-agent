import random
import re
from typing import Optional, List
try:
    from .config import config
    from .database import add_log, get_recent_reply_texts
except (ImportError, ValueError):
    from config import config
    from database import add_log, get_recent_reply_texts

class CommentGenerator:
    def __init__(self):
        self._recent_comments_cache: List[str] = []

    def generate(self, tweet_text: str, author: str, keyword: str = "") -> str:
        """
        Generates an authentic, ultra-short, human-like reaction to an X post.
        Prioritizes OpenAI LLM for tailored commentary (4-10 words).
        Falls back to a high-entropy dynamic template generator (5-9 words).
        Guarantees zero repetitive spam n-grams and human-like brevity.
        """
        clean_text = self._sanitize_text(tweet_text)
        recent_history = self._get_recent_history()
        
        # 1. Try AI contextual generation with OpenAI
        if config.openai_api_key and config.openai_api_key.strip():
            for attempt in range(2):
                try:
                    ai_comment = self._generate_with_llm(clean_text, author, keyword)
                    if ai_comment and 6 <= len(ai_comment) <= 80:
                        if not self._is_too_similar(ai_comment, recent_history):
                            self._record_generated(ai_comment)
                            add_log("INFO", f"Generated short AI comment for @{author}")
                            return ai_comment
                except Exception as e:
                    add_log("WARN", f"LLM generation attempt {attempt + 1} failed ({str(e)}).")

        # 2. Fallback to high-entropy dynamic template generator
        for _ in range(5):
            template_comment = self._generate_with_dynamic_templates(clean_text, author, keyword)
            if not self._is_too_similar(template_comment, recent_history):
                self._record_generated(template_comment)
                add_log("INFO", f"Generated short dynamic comment for @{author}")
                return template_comment

        fallback = self._generate_with_dynamic_templates(clean_text, author, keyword)
        self._record_generated(fallback)
        return fallback

    def _sanitize_text(self, text: str) -> str:
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _get_recent_history(self) -> List[str]:
        try:
            db_history = get_recent_reply_texts(limit=40)
        except Exception:
            db_history = []
        combined = list(set(self._recent_comments_cache[-30:] + db_history))
        return combined

    def _record_generated(self, comment: str):
        self._recent_comments_cache.append(comment)
        if len(self._recent_comments_cache) > 60:
            self._recent_comments_cache.pop(0)

    def _is_too_similar(self, new_comment: str, history: List[str]) -> bool:
        if not history:
            return False
        
        new_words = set(re.findall(r'\b\w+\b', new_comment.lower()))
        if len(new_words) < 3:
            return False

        for past in history:
            past_words = set(re.findall(r'\b\w+\b', past.lower()))
            if not past_words:
                continue
            intersection = new_words.intersection(past_words)
            union = new_words.union(past_words)
            similarity = len(intersection) / len(union) if union else 0.0
            if similarity > 0.65:
                return True
        return False

    def _generate_with_llm(self, text: str, author: str, keyword: str) -> Optional[str]:
        from openai import OpenAI
        client = OpenAI(api_key=config.openai_api_key)

        prompt = f"""You are replying to a post on X (Twitter).
Author: @{author}
Post text: "{text}"
Topic: {keyword or 'crypto/markets'}

Task:
Write an ULTRA-SHORT, punchy 1-sentence reaction to this tweet.

Strict Protection & Anti-Ban Rules:
1. Length: ULTRA-SHORT (between 4 and 10 words maximum). Real people on X write very brief, casual reactions. DO NOT write a paragraph or more than 1 short sentence.
2. Grounding: Directly react to the tweet's specific point, chart, ticker, or sentiment.
3. NO MARKETING: Never say "market news", "check out", "take a look", "signals", "DM me", or "join".
4. NO PROMOTIONAL LINKS, URLS, OR HASHTAGS (#).
5. Tone: Casual, knowledgeable trader peer.
6. Language: Match the EXACT language of the tweet (if in Spanish, reply in Spanish; if in Japanese, in Japanese; if in English, in English).
7. Output ONLY the raw short comment text without quotation marks or explanations."""

        response = client.chat.completions.create(
            model=config.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a real crypto and market trader casually commenting on X. You write ultra-brief, punchy 1-sentence comments (4 to 10 words) that sound 100% human and casual."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=25,
            temperature=0.85
        )

        comment = response.choices[0].message.content.strip()
        comment = comment.strip('"\'')
        comment = re.sub(r'#\w+', '', comment).strip()
        if len(comment) > 90:
            comment = comment[:87] + "..."
        return comment

    def _generate_with_dynamic_templates(self, text: str, author: str, keyword: str) -> str:
        """
        Produces short, punchy 4-9 word comments tailored to market topics.
        """
        text_lower = text.lower()

        is_btc = "btc" in text_lower or "bitcoin" in text_lower
        is_eth = "eth" in text_lower or "ethereum" in text_lower or "sol" in text_lower
        is_macro = "fed" in text_lower or "inflation" in text_lower or "cpi" in text_lower or "rate" in text_lower
        is_stock = "stock" in text_lower or "nvda" in text_lower or "spx" in text_lower or "nasdaq" in text_lower

        if is_btc:
            templates = [
                "Solid breakdown. Watching that weekly close closely.",
                "Spot volume looking pretty clean here.",
                "Clean level. Let's see if buyers defend this.",
                "Decent bounce, watching that 4h candle close.",
                "Fair point, funding rates will tell the real story.",
                "Watching this pivot closely for confirmation.",
                "Liquidity grab right here makes a lot of sense.",
                "Good call. Watching that next resistance zone.",
                "Volume shelf at this level is strong."
            ]
        elif is_eth:
            templates = [
                "L2 activity has been picking up nicely here.",
                "The ETH/BTC ratio is at a critical spot.",
                "Clean structure. Watching the daily close.",
                "Decent consolidation, let's see if momentum holds.",
                "On-chain flows looking pretty steady at this level.",
                "Valid point, ecosystem volume is definitely showing up."
            ]
        elif is_macro:
            templates = [
                "Macro data this week will likely decide this move.",
                "Bond yields are definitely driving the bus right now.",
                "Seems mostly priced in, but watching NY open.",
                "DXY reaction tomorrow will be the real tell.",
                "Fair take, volatility around the announcement will be high."
            ]
        elif is_stock:
            templates = [
                "Earnings momentum is strong, watching market breadth.",
                "Clean gap fill setup on the daily chart.",
                "Sector rotation here is definitely interesting to watch.",
                "Volume shelf at this pivot makes sense.",
                "Good observation, keeping an eye on the continuation."
            ]
        else:
            templates = [
                "Solid point. Watching how this level holds.",
                "Clean chart. What's your invalidation level on this?",
                "Fair assessment, waiting for the daily confirmation.",
                "Interesting setup, watching the London and NY open.",
                "Patience is key at these key inflection levels.",
                "Decent price action, let's see if buyers step in.",
                "Good call, volume looks pretty constructive here.",
                "Definitely a key level to monitor today."
            ]

        return random.choice(templates)

generator = CommentGenerator()
