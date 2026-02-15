import time as time_mod
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_servers.reddit.scraper import (
    TICKER_BLACKLIST,
    RedditScraper,
    extract_tickers,
    score_sentiment,
)

# --- extract_tickers ---


class TestExtractTickers:
    def test_dollar_prefix(self):
        result = extract_tickers("I'm buying $AAPL and $MSFT")
        assert "AAPL" in result
        assert "MSFT" in result

    def test_plain_ticker(self):
        result = extract_tickers("ASML is looking strong today")
        assert "ASML" in result

    def test_eu_suffix(self):
        result = extract_tickers("Check out ASML.AS and SAP.DE")
        assert "ASML.AS" in result
        assert "SAP.DE" in result

    def test_blacklisted_words_excluded(self):
        result = extract_tickers("The CEO announced an IPO for the ETF")
        assert "CEO" not in result
        assert "IPO" not in result
        assert "ETF" not in result

    def test_all_blacklist_items_excluded(self):
        for word in list(TICKER_BLACKLIST)[:10]:
            result = extract_tickers(f"Something about {word} today")
            assert word not in result

    def test_no_tickers(self):
        assert extract_tickers("no stocks mentioned here at all") == []

    def test_deduplication(self):
        result = extract_tickers("ASML ASML ASML mentioned three times")
        assert result.count("ASML") == 1

    def test_short_words_excluded(self):
        # Single letter words should not match (pattern requires 2+ chars)
        result = extract_tickers("I A B")
        assert result == []

    def test_mixed_case_only_uppercase(self):
        # Pattern only matches uppercase
        result = extract_tickers("asml Asml ASML")
        assert result == ["ASML"]

    def test_multiple_eu_tickers(self):
        result = extract_tickers("RDSA.L vs SHELL.AS which is better?")
        assert "RDSA.L" in result
        assert "SHELL.AS" in result


# --- score_sentiment ---


class TestScoreSentiment:
    def test_bullish_text(self):
        score = score_sentiment("going to the moon, very bullish, buy now")
        assert score > 0

    def test_bearish_text(self):
        score = score_sentiment("crash incoming, sell everything, overvalued")
        assert score < 0

    def test_neutral_text(self):
        score = score_sentiment("the company reported earnings today")
        assert score == 0.0

    def test_score_bounds(self):
        # Even extreme text should stay within [-1, 1]
        bullish = score_sentiment(
            " ".join(["buy bullish moon rocket calls long undervalued breakout"] * 10),
            upvotes=10000,
        )
        bearish = score_sentiment(
            " ".join(["sell bearish puts short overvalued crash dump"] * 10),
            upvotes=10000,
        )
        assert -1.0 <= bullish <= 1.0
        assert -1.0 <= bearish <= 1.0

    def test_upvote_weighting(self):
        low = score_sentiment("bullish buy calls", upvotes=1)
        high = score_sentiment("bullish buy calls", upvotes=200)
        assert low > 0
        assert high > 0
        assert high >= low

    def test_single_upvote_no_boost(self):
        score1 = score_sentiment("bullish", upvotes=1)
        score0 = score_sentiment("bullish", upvotes=0)
        # upvotes=0 should still work without error
        assert score1 > 0
        assert score0 > 0

    def test_mixed_sentiment(self):
        score = score_sentiment("bullish on calls but might crash and sell")
        # Has both bullish and bearish keywords — result depends on ratio
        assert -1.0 <= score <= 1.0


# --- RedditScraper (mocked asyncpraw) ---


def _make_mock_submission(
    post_id: str = "abc123",
    title: str = "ASML is going to the moon",
    selftext: str = "Very bullish on ASML.AS",
    score: int = 100,
    num_comments: int = 50,
    created_utc: float | None = None,
    permalink: str = "/r/stocks/comments/abc123/asml_moon/",
):
    sub = MagicMock()
    sub.id = post_id
    sub.title = title
    sub.selftext = selftext
    sub.score = score
    sub.num_comments = num_comments
    sub.created_utc = created_utc or time_mod.time()
    sub.permalink = permalink
    return sub


def _make_mock_comment(
    comment_id: str = "com1",
    body: str = "Great analysis!",
    score: int = 10,
    author: str = "testuser",
    created_utc: float | None = None,
):
    comment = MagicMock()
    comment.id = comment_id
    comment.body = body
    comment.score = score
    comment.author = MagicMock(__str__=lambda self: author)
    comment.created_utc = created_utc or time_mod.time()
    return comment


class _AsyncSubmissionIterator:
    """Async iterator over a list of mock submissions."""

    def __init__(self, items: list):
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class TestRedditScraperSearch:
    @pytest.mark.asyncio
    async def test_search_subreddit_returns_posts(self):
        submissions = [
            _make_mock_submission(post_id="1", title="ASML bullish"),
            _make_mock_submission(post_id="2", title="SAP.DE analysis"),
        ]

        mock_sub = MagicMock()
        mock_sub.search = MagicMock(return_value=_AsyncSubmissionIterator(submissions))

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        results = await scraper.search_subreddit("stocks", "ASML", limit=10)
        assert len(results) == 2
        assert results[0]["id"] == "1"
        assert results[0]["title"] == "ASML bullish"
        assert "url" in results[0]
        assert "created_utc" in results[0]


class TestRedditScraperTrending:
    @pytest.mark.asyncio
    async def test_get_trending_tickers(self):
        submissions = [
            _make_mock_submission(title="ASML to the moon!", selftext="Buy ASML.AS now"),
            _make_mock_submission(title="ASML is amazing", selftext="Very bullish"),
            _make_mock_submission(title="SAP.DE earnings", selftext="Looking at SAP"),
        ]

        mock_sub = MagicMock()
        mock_sub.hot = MagicMock(return_value=_AsyncSubmissionIterator(submissions))

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        results = await scraper.get_trending_tickers(subreddits=["stocks"], hours=24)
        assert isinstance(results, list)
        assert len(results) > 0
        # ASML should be most mentioned
        tickers = [r["ticker"] for r in results]
        assert "ASML" in tickers

    @pytest.mark.asyncio
    async def test_filters_old_posts(self):
        old_time = time_mod.time() - 48 * 3600  # 48 hours ago
        submissions = [
            _make_mock_submission(title="ASML old post", created_utc=old_time),
        ]

        mock_sub = MagicMock()
        mock_sub.hot = MagicMock(return_value=_AsyncSubmissionIterator(submissions))

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        results = await scraper.get_trending_tickers(subreddits=["stocks"], hours=24)
        assert results == []


class TestRedditScraperComments:
    @pytest.mark.asyncio
    async def test_get_post_comments(self):
        comments = [
            _make_mock_comment(comment_id="c1", body="Great DD!"),
            _make_mock_comment(comment_id="c2", body="I disagree"),
        ]

        mock_comments = MagicMock()
        mock_comments.replace_more = MagicMock()
        mock_comments.__getitem__ = lambda self, key: comments[key]

        mock_submission = AsyncMock()
        mock_submission.load = AsyncMock()
        mock_submission.comments = mock_comments

        mock_reddit = AsyncMock()
        mock_reddit.submission = AsyncMock(return_value=mock_submission)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        results = await scraper.get_post_comments("abc123", limit=50)
        assert len(results) == 2
        assert results[0]["id"] == "c1"
        assert results[0]["body"] == "Great DD!"
        assert results[1]["author"] == "testuser"


class TestRedditScraperDailyDigest:
    @pytest.mark.asyncio
    async def test_daily_digest_structure(self):
        submissions = [
            _make_mock_submission(title="ASML bullish moon buy", score=200),
            _make_mock_submission(title="SAP.DE crash sell", score=50),
        ]

        mock_sub = MagicMock()
        mock_sub.hot = MagicMock(return_value=_AsyncSubmissionIterator(submissions))

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        digest = await scraper.get_daily_digest(subreddits=["stocks"])
        assert "date" in digest
        assert "subreddits_scraped" in digest
        assert "total_posts" in digest
        assert "tickers" in digest
        assert digest["total_posts"] == 2
        assert digest["subreddits_scraped"] == ["stocks"]
        assert isinstance(digest["tickers"], list)

    @pytest.mark.asyncio
    async def test_daily_digest_sentiment_direction(self):
        submissions = [
            _make_mock_submission(
                title="BULL stock buy bullish moon rocket calls",
                selftext="Very bullish breakout long",
                score=100,
            ),
        ]

        mock_sub = MagicMock()
        mock_sub.hot = MagicMock(return_value=_AsyncSubmissionIterator(submissions))

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        scraper = RedditScraper("id", "secret", "agent")
        scraper._reddit = mock_reddit

        digest = await scraper.get_daily_digest(subreddits=["stocks"])
        # BULL ticker should have positive sentiment
        bull_tickers = [t for t in digest["tickers"] if t["ticker"] == "BULL"]
        assert len(bull_tickers) == 1
        assert bull_tickers[0]["sentiment_score"] > 0
