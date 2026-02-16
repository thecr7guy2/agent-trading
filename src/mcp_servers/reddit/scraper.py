import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "investing",
    "stocks",
    "EuropeanStocks",
    "Euronext",
    "eupersonalfinance",
    "SecurityAnalysis",
    "stockmarket",
    "ValueInvesting",
    "dividends",
    "options",
]

TICKER_PATTERN = re.compile(r"\$?([A-Z]{2,5}(?:\.[A-Z]{1,2})?)\b")

TICKER_BLACKLIST = {
    "CEO",
    "IPO",
    "ETF",
    "NYSE",
    "USD",
    "EUR",
    "GDP",
    "AI",
    "DD",
    "YOLO",
    "IMO",
    "FYI",
    "HODL",
    "ATH",
    "ATL",
    "ITM",
    "OTM",
    "PE",
    "EPS",
    "SEC",
    "FDA",
    "CPI",
    "FED",
    "API",
    "RSI",
    "MACD",
    "EMA",
    "SMA",
    "FOMO",
    "WSB",
    "DCA",
    "RH",
    "PM",
    "AM",
    "US",
    "EU",
    "UK",
    "LLC",
    "INC",
    "THE",
    "FOR",
    "AND",
    "NOT",
    "ARE",
    "HAS",
    "WAS",
    "BUT",
    "ALL",
    "CAN",
    "HAD",
    "HER",
    "ONE",
    "OUR",
    "OUT",
    "YOU",
    "HIS",
    "HOW",
    "ITS",
    "LET",
    "MAY",
    "NEW",
    "NOW",
    "OLD",
    "SEE",
    "WAY",
    "WHO",
    "BOY",
    "DID",
    "GET",
    "HIM",
    "SAY",
    "SHE",
    "TOO",
    "USE",
}

BULLISH_KEYWORDS = [
    "buy",
    "bullish",
    "moon",
    "rocket",
    "calls",
    "long",
    "undervalued",
    "breakout",
    "squeeze",
    "upside",
    "growth",
    "strong",
    "rally",
    "pump",
    "gain",
    "profit",
    "green",
    "support",
    "accumulate",
]

BEARISH_KEYWORDS = [
    "sell",
    "bearish",
    "puts",
    "short",
    "overvalued",
    "crash",
    "dump",
    "bag",
    "downside",
    "weak",
    "decline",
    "drop",
    "red",
    "resistance",
    "loss",
    "falling",
    "bubble",
    "avoid",
    "exit",
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_tickers(text: str) -> list[str]:
    matches = TICKER_PATTERN.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for ticker in matches:
        if ticker not in TICKER_BLACKLIST and ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def score_sentiment(text: str, upvotes: int = 1) -> float:
    text_lower = text.lower()
    bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    total = bullish_count + bearish_count
    if total == 0:
        return 0.0
    raw_score = (bullish_count - bearish_count) / total
    # Weight by log of upvotes to dampen extreme scaling
    weight = min(1.0 + (upvotes - 1) * 0.01, 2.0) if upvotes > 1 else 1.0
    return max(-1.0, min(1.0, raw_score * weight))


def strip_html(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def extract_post_id(entry: dict) -> str:
    atom_id = entry.get("id", "")
    if atom_id.startswith("t3_"):
        return atom_id
    # Try extracting from link
    link = entry.get("link", "")
    parts = link.rstrip("/").split("/")
    # Reddit comment URLs: .../comments/<id>/...
    if "comments" in parts:
        idx = parts.index("comments")
        if idx + 1 < len(parts):
            return f"t3_{parts[idx + 1]}"
    return atom_id or link


@dataclass
class RedditPost:
    id: str
    title: str
    body: str
    author: str
    subreddit: str
    url: str
    published: datetime


@dataclass
class RSSCollector:
    user_agent: str = "trading-bot/1.0"
    _posts: dict[str, RedditPost] = field(default_factory=dict)
    _collection_rounds: int = 0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
    ) -> httpx.Response:
        async with semaphore:
            await asyncio.sleep(0.2)
            resp = await client.get(url)
            resp.raise_for_status()
            return resp

    async def _fetch_feed(
        self, client: httpx.AsyncClient, subreddit: str, sort: str, semaphore: asyncio.Semaphore
    ) -> list[RedditPost]:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.rss"
        posts: list[RedditPost] = []
        try:
            resp = await self._fetch_with_retry(client, url, semaphore)
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch %s after retries: %s", url, e)
            return posts

        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            post_id = extract_post_id(entry)
            # Get body from content or summary
            body_html = ""
            if hasattr(entry, "content") and entry.content:
                body_html = entry.content[0].get("value", "")
            elif hasattr(entry, "summary"):
                body_html = entry.summary or ""

            author = getattr(entry, "author", "").removeprefix("/u/")

            published = datetime.now(UTC)
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=UTC)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=UTC)

            posts.append(
                RedditPost(
                    id=post_id,
                    title=entry.get("title", ""),
                    body=strip_html(body_html),
                    author=author,
                    subreddit=subreddit,
                    url=entry.get("link", ""),
                    published=published,
                )
            )
        return posts

    async def collect(self, subreddits: list[str] | None = None) -> dict:
        subreddits = subreddits or DEFAULT_SUBREDDITS
        semaphore = asyncio.Semaphore(5)
        new_count = 0

        async with httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=15.0,
        ) as client:
            tasks = [
                self._fetch_feed(client, sub, sort, semaphore)
                for sub in subreddits
                for sort in ("hot", "new", "top")
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Feed fetch error: %s", result)
                continue
            for post in result:
                if post.id not in self._posts:
                    self._posts[post.id] = post
                    new_count += 1

        self._collection_rounds += 1
        return {
            "round": self._collection_rounds,
            "new_posts": new_count,
            "total_posts": len(self._posts),
            "subreddits": subreddits,
        }

    def get_daily_digest(self, subreddits: list[str] | None = None) -> dict:
        ticker_data: dict[str, dict] = {}
        posts = self._posts.values()

        if subreddits:
            sub_set = set(subreddits)
            posts = [p for p in posts if p.subreddit in sub_set]

        for post in posts:
            text = f"{post.title} {post.body}"
            tickers = extract_tickers(text)
            for ticker in tickers:
                if ticker not in ticker_data:
                    ticker_data[ticker] = {
                        "ticker": ticker,
                        "mentions": 0,
                        "sentiment_total": 0.0,
                        "sentiment_count": 0,
                        "top_posts": [],
                    }
                entry = ticker_data[ticker]
                entry["mentions"] += 1
                sentiment = score_sentiment(text)
                entry["sentiment_total"] += sentiment
                entry["sentiment_count"] += 1
                if len(entry["top_posts"]) < 3:
                    entry["top_posts"].append(
                        {
                            "title": post.title,
                            "url": post.url,
                            "subreddit": post.subreddit,
                        }
                    )

        tickers_list = []
        for data in sorted(ticker_data.values(), key=lambda x: x["mentions"], reverse=True):
            count = data["sentiment_count"]
            avg_sentiment = round(data["sentiment_total"] / count, 3) if count else 0.0
            tickers_list.append(
                {
                    "ticker": data["ticker"],
                    "mentions": data["mentions"],
                    "sentiment_score": avg_sentiment,
                    "top_posts": data["top_posts"],
                }
            )

        scraped_subs = subreddits or list({p.subreddit for p in self._posts.values()})
        return {
            "date": datetime.now(UTC).strftime("%Y-%m-%d"),
            "subreddits_scraped": scraped_subs,
            "total_posts": len(list(posts)),
            "tickers": tickers_list,
        }

    def get_collection_stats(self) -> dict:
        per_sub: dict[str, int] = {}
        for post in self._posts.values():
            per_sub[post.subreddit] = per_sub.get(post.subreddit, 0) + 1
        return {
            "collection_rounds": self._collection_rounds,
            "total_posts": len(self._posts),
            "per_subreddit": per_sub,
        }

    def reset(self):
        self._posts.clear()
        self._collection_rounds = 0
