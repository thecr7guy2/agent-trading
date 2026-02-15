import re
import time as time_mod
from datetime import UTC, datetime

import asyncpraw

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "investing",
    "stocks",
    "EuropeanStocks",
    "Euronext",
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


class RedditScraper:
    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self._reddit: asyncpraw.Reddit | None = None
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent

    async def _get_reddit(self) -> asyncpraw.Reddit:
        if self._reddit is None:
            self._reddit = asyncpraw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
        return self._reddit

    async def close(self):
        if self._reddit is not None:
            await self._reddit.close()
            self._reddit = None

    async def search_subreddit(self, subreddit: str, query: str, limit: int = 25) -> list[dict]:
        reddit = await self._get_reddit()
        sub = await reddit.subreddit(subreddit)
        posts = []
        async for submission in sub.search(query, sort="relevance", limit=limit):
            posts.append(
                {
                    "id": submission.id,
                    "title": submission.title,
                    "score": submission.score,
                    "url": f"https://reddit.com{submission.permalink}",
                    "num_comments": submission.num_comments,
                    "created_utc": datetime.fromtimestamp(
                        submission.created_utc, tz=UTC
                    ).isoformat(),
                    "selftext_preview": (submission.selftext or "")[:500],
                }
            )
        return posts

    async def get_trending_tickers(
        self, subreddits: list[str] | None = None, hours: int = 24
    ) -> list[dict]:
        subreddits = subreddits or DEFAULT_SUBREDDITS
        cutoff = time_mod.time() - hours * 3600
        ticker_data: dict[str, dict] = {}

        for sub_name in subreddits:
            reddit = await self._get_reddit()
            sub = await reddit.subreddit(sub_name)
            async for submission in sub.hot(limit=100):
                if submission.created_utc < cutoff:
                    continue
                text = f"{submission.title} {submission.selftext or ''}"
                tickers = extract_tickers(text)
                for ticker in tickers:
                    if ticker not in ticker_data:
                        ticker_data[ticker] = {
                            "ticker": ticker,
                            "mention_count": 0,
                            "subreddits": {},
                            "total_score": 0,
                        }
                    ticker_data[ticker]["mention_count"] += 1
                    ticker_data[ticker]["total_score"] += submission.score
                    sub_counts = ticker_data[ticker]["subreddits"]
                    sub_counts[sub_name] = sub_counts.get(sub_name, 0) + 1

        result = sorted(ticker_data.values(), key=lambda x: x["mention_count"], reverse=True)
        for item in result:
            count = item["mention_count"]
            item["avg_score"] = round(item.pop("total_score") / count, 1) if count else 0
        return result

    async def get_post_comments(self, post_id: str, limit: int = 50) -> list[dict]:
        reddit = await self._get_reddit()
        submission = await reddit.submission(id=post_id)
        await submission.load()
        submission.comments.replace_more(limit=0)
        comments = []
        for comment in submission.comments[:limit]:
            comments.append(
                {
                    "id": comment.id,
                    "body": comment.body,
                    "score": comment.score,
                    "author": str(comment.author) if comment.author else "[deleted]",
                    "created_utc": datetime.fromtimestamp(comment.created_utc, tz=UTC).isoformat(),
                }
            )
        return comments

    async def get_daily_digest(self, subreddits: list[str] | None = None) -> dict:
        subreddits = subreddits or DEFAULT_SUBREDDITS
        ticker_data: dict[str, dict] = {}
        total_posts = 0

        for sub_name in subreddits:
            reddit = await self._get_reddit()
            sub = await reddit.subreddit(sub_name)
            async for submission in sub.hot(limit=100):
                total_posts += 1
                text = f"{submission.title} {submission.selftext or ''}"
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
                    sentiment = score_sentiment(text, submission.score)
                    entry["sentiment_total"] += sentiment
                    entry["sentiment_count"] += 1
                    if len(entry["top_posts"]) < 3:
                        entry["top_posts"].append(
                            {
                                "title": submission.title,
                                "score": submission.score,
                                "url": f"https://reddit.com{submission.permalink}",
                                "subreddit": sub_name,
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

        return {
            "date": datetime.now(UTC).strftime("%Y-%m-%d"),
            "subreddits_scraped": subreddits,
            "total_posts": total_posts,
            "tickers": tickers_list,
        }
