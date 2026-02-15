from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.mcp_servers.reddit.scraper import (
    TICKER_BLACKLIST,
    RedditPost,
    RSSCollector,
    extract_post_id,
    extract_tickers,
    score_sentiment,
    strip_html,
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


# --- strip_html ---


class TestStripHTML:
    def test_removes_tags(self):
        assert strip_html("<p>hello</p>") == "hello"

    def test_nested_tags(self):
        assert strip_html("<div><b>bold</b> text</div>") == "bold text"

    def test_unescapes_entities(self):
        assert strip_html("&amp; &lt; &gt; &quot;") == '& < > "'

    def test_collapses_whitespace(self):
        assert strip_html("  too   many   spaces  ") == "too many spaces"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_plain_text_passthrough(self):
        assert strip_html("no html here") == "no html here"


# --- extract_post_id ---


class TestExtractPostId:
    def test_t3_prefix(self):
        assert extract_post_id({"id": "t3_abc123"}) == "t3_abc123"

    def test_url_fallback(self):
        entry = {
            "id": "some-atom-id",
            "link": "https://www.reddit.com/r/stocks/comments/xyz789/some_title/",
        }
        assert extract_post_id(entry) == "t3_xyz789"

    def test_no_id_uses_link(self):
        entry = {"link": "https://www.reddit.com/r/stocks/comments/abc/title/"}
        assert extract_post_id(entry) == "t3_abc"

    def test_bare_atom_id_no_comments_in_link(self):
        entry = {"id": "some-id", "link": "https://example.com/no-comments-path"}
        assert extract_post_id(entry) == "some-id"

    def test_empty_entry(self):
        assert extract_post_id({}) == ""


# --- RSSCollector ---

SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_post1</id>
    <title>ASML is going to the moon bullish buy</title>
    <link href="https://www.reddit.com/r/stocks/comments/post1/asml_moon/"/>
    <author><name>/u/trader1</name></author>
    <content type="html">&lt;p&gt;Very bullish on ASML.AS, breakout incoming&lt;/p&gt;</content>
    <updated>2026-02-15T10:00:00+00:00</updated>
  </entry>
  <entry>
    <id>t3_post2</id>
    <title>SAP.DE crash sell overvalued bearish</title>
    <link href="https://www.reddit.com/r/stocks/comments/post2/sap_crash/"/>
    <author><name>/u/trader2</name></author>
    <content type="html">&lt;p&gt;SAP is way overvalued, crash coming&lt;/p&gt;</content>
    <updated>2026-02-15T11:00:00+00:00</updated>
  </entry>
</feed>"""

SAMPLE_ATOM_XML_2 = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_post3</id>
    <title>NVDA strong growth rally</title>
    <link href="https://www.reddit.com/r/investing/comments/post3/nvda/"/>
    <author><name>/u/trader3</name></author>
    <content type="html">&lt;p&gt;NVDA looking strong&lt;/p&gt;</content>
    <updated>2026-02-15T12:00:00+00:00</updated>
  </entry>
</feed>"""


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code, text=text, request=httpx.Request("GET", "http://x")
    )


class TestRSSCollectorCollect:
    @pytest.mark.asyncio
    async def test_collect_accumulates_posts(self):
        collector = RSSCollector()

        async def mock_get(url, **kwargs):
            return _mock_response(SAMPLE_ATOM_XML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = mock_get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            result = await collector.collect(subreddits=["stocks"])

        assert result["round"] == 1
        assert result["total_posts"] >= 2
        assert result["new_posts"] >= 2

    @pytest.mark.asyncio
    async def test_collect_deduplicates_across_rounds(self):
        collector = RSSCollector()

        async def mock_get(url, **kwargs):
            return _mock_response(SAMPLE_ATOM_XML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = mock_get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            r1 = await collector.collect(subreddits=["stocks"])
            r2 = await collector.collect(subreddits=["stocks"])

        assert r2["round"] == 2
        assert r2["new_posts"] == 0  # Same posts, all deduped
        assert r2["total_posts"] == r1["total_posts"]

    @pytest.mark.asyncio
    async def test_collect_handles_http_errors(self):
        collector = RSSCollector()

        async def mock_get(url, **kwargs):
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("GET", url), response=_mock_response("", 429)
            )

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = mock_get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            result = await collector.collect(subreddits=["stocks"])

        assert result["round"] == 1
        assert result["total_posts"] == 0


class TestRSSCollectorDigest:
    def _make_collector_with_posts(self) -> RSSCollector:
        collector = RSSCollector()
        collector._posts = {
            "t3_1": RedditPost(
                id="t3_1",
                title="ASML bullish moon buy rocket",
                body="Very bullish breakout on ASML.AS",
                author="user1",
                subreddit="stocks",
                url="https://reddit.com/r/stocks/comments/1/asml/",
                published=datetime(2026, 2, 15, 10, 0, tzinfo=UTC),
            ),
            "t3_2": RedditPost(
                id="t3_2",
                title="SAP.DE crash sell overvalued",
                body="SAP is dumping avoid",
                author="user2",
                subreddit="investing",
                url="https://reddit.com/r/investing/comments/2/sap/",
                published=datetime(2026, 2, 15, 11, 0, tzinfo=UTC),
            ),
        }
        collector._collection_rounds = 1
        return collector

    def test_digest_structure(self):
        collector = self._make_collector_with_posts()
        digest = collector.get_daily_digest()

        assert "date" in digest
        assert "subreddits_scraped" in digest
        assert "total_posts" in digest
        assert "tickers" in digest
        assert digest["total_posts"] == 2
        assert isinstance(digest["tickers"], list)

    def test_digest_sentiment_direction(self):
        collector = self._make_collector_with_posts()
        digest = collector.get_daily_digest()

        asml_tickers = [t for t in digest["tickers"] if t["ticker"] == "ASML"]
        assert len(asml_tickers) == 1
        assert asml_tickers[0]["sentiment_score"] > 0

    def test_digest_bearish_sentiment(self):
        collector = self._make_collector_with_posts()
        digest = collector.get_daily_digest()

        sap_tickers = [t for t in digest["tickers"] if t["ticker"] == "SAP"]
        assert len(sap_tickers) == 1
        assert sap_tickers[0]["sentiment_score"] < 0

    def test_digest_subreddit_filter(self):
        collector = self._make_collector_with_posts()
        digest = collector.get_daily_digest(subreddits=["stocks"])

        assert digest["total_posts"] == 1
        assert digest["subreddits_scraped"] == ["stocks"]

    def test_digest_empty_collection(self):
        collector = RSSCollector()
        digest = collector.get_daily_digest()

        assert digest["total_posts"] == 0
        assert digest["tickers"] == []


class TestRSSCollectorStats:
    def test_stats_empty(self):
        collector = RSSCollector()
        stats = collector.get_collection_stats()

        assert stats["collection_rounds"] == 0
        assert stats["total_posts"] == 0
        assert stats["per_subreddit"] == {}

    def test_stats_after_collection(self):
        collector = RSSCollector()
        collector._posts = {
            "t3_1": RedditPost(
                id="t3_1",
                title="Post 1",
                body="body",
                author="u1",
                subreddit="stocks",
                url="http://x",
                published=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "t3_2": RedditPost(
                id="t3_2",
                title="Post 2",
                body="body",
                author="u2",
                subreddit="investing",
                url="http://x",
                published=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "t3_3": RedditPost(
                id="t3_3",
                title="Post 3",
                body="body",
                author="u3",
                subreddit="stocks",
                url="http://x",
                published=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        }
        collector._collection_rounds = 2

        stats = collector.get_collection_stats()

        assert stats["collection_rounds"] == 2
        assert stats["total_posts"] == 3
        assert stats["per_subreddit"]["stocks"] == 2
        assert stats["per_subreddit"]["investing"] == 1


class TestRSSCollectorReset:
    def test_reset_clears_state(self):
        collector = RSSCollector()
        collector._posts["t3_1"] = RedditPost(
            id="t3_1",
            title="x",
            body="y",
            author="u",
            subreddit="s",
            url="http://x",
            published=datetime(2026, 1, 1, tzinfo=UTC),
        )
        collector._collection_rounds = 3

        collector.reset()

        assert len(collector._posts) == 0
        assert collector._collection_rounds == 0
        assert collector.get_collection_stats()["total_posts"] == 0
