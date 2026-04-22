# I Built a Bot That Follows Public Insider Buying and Congressional Disclosures

Most trading bots chase price action, news headlines, or technical patterns. I wanted to test something simpler: what happens if you systematically follow public buy disclosures from people who are closest to the action?

That is the idea behind this project.

Twice a week, the bot pulls two kinds of public signals:

- Corporate insider buys surfaced through OpenInsider, which aggregates SEC Form 4 filings
- US House and Senate trade disclosures surfaced through Capitol Trades

It scores those signals, enriches them with market data, sends the final candidate list to Claude Opus, and places buy orders in a Trading 212 demo account. Then it updates a public dashboard so I can see what it did and how the portfolio is performing.

The important wording here is "surfaced through OpenInsider." The bot does not currently scrape the SEC directly. It uses OpenInsider as the source for SEC-derived insider transactions and Capitol Trades as the source for House and Senate disclosures. That distinction matters, and I wanted the write-up to reflect the code accurately.

## Why These Two Data Sources

The whole strategy is built around one idea: follow people who are committing their own capital and might know more than the market does.

For corporate insiders, the signal is intuitive. When a CEO, CFO, COO, chair, or director buys shares in their own company with personal money, that is usually more meaningful than a talking head on TV saying a stock looks attractive. These people know their business better than anyone else. If several of them are buying around the same time, that gets even more interesting.

For members of Congress, the logic is different but still worth testing. Congressional disclosures are delayed and noisy, so they are not clean alpha by default. But elected officials do sit close to regulation, funding, and policy. If a congressional disclosure overlaps with a corporate insider signal on the same ticker, that becomes a much stronger event than either source alone.

I am not claiming this is secret information. Quite the opposite. The point of the bot is to take public disclosures that most people do not monitor consistently, process them the same way every run, and test whether disciplined attention to those signals produces a useful edge.

## What The Code Actually Does

The pipeline is fairly direct.

First, it fetches recent buy transactions from OpenInsider and recent buy disclosures from Capitol Trades in parallel. Then it groups both sources by ticker and calculates a conviction score for each one.

For OpenInsider, the score is based on three things:

- How much ownership increased
- Whether the buyer is a senior operator such as a CEO or CFO
- How recent the trade was

In the current implementation, C-suite style titles get a 3x multiplier. Recent trades score higher than older ones. A ticker can qualify because it is a cluster buy with two or more insiders, because a solo C-suite insider meaningfully increased ownership, or because a single buyer put at least $200K to work.

For Capitol Trades, the code parses the disclosed trade range, takes the midpoint, and applies a recency decay. It is a rougher signal than Form 4 data, but it is still useful as an additional source of conviction.

After both source lists are built, the bot merges overlapping tickers. If the same stock appears in both sources, the conviction scores and disclosed values are combined into one candidate. That is one of the most interesting cases in the whole system: a stock where company insiders and congressional filers are both buying.

Then the bot enriches every candidate with:

- Fundamentals
- Technical indicators
- Recent news
- Upcoming earnings
- Insider buy history for that ticker

At that point, Claude Opus gets the full candidate digest plus the current portfolio and the budget for the run.

There is no multi-stage analyst stack in the active pipeline right now. The current code sends the enriched candidates directly to Claude for the decision.

## Claude's Job In The System

Claude is not there to hallucinate a macro story or invent hidden catalysts. Its role is much narrower and more useful than that.

It acts as a portfolio manager sitting on top of the signal engine.

The prompt tells Claude to prioritize:

1. Insider conviction first
2. Price context second
3. Fundamentals and technicals as a sanity check

That means a CEO doubling their stake after a large drawdown matters more than a random stock that just looks cheap on a screener. The model returns a set of buy picks, allocation percentages, a confidence score, and written reasoning for each decision.

There is also an implementation detail that matters: if there are Capitol Trades candidates in the pool and Claude does not choose any of them, the orchestrator can inject the top Capitol Trades name by replacing the weakest buy pick. So the congressional signal is not just "available"; it is deliberately kept in the decision set.

## Filters That Matter

A lot of the quality of this project comes from what it ignores.

The bot only keeps open-market purchases from the insider feed. It also filters out non-equity instruments such as ETFs and mutual funds after enrichment. On the congressional side, it drops pure Capitol Trades candidates above the market-cap ceiling, which defaults to $50B. The reason is simple: massive liquid names often look more like generic portfolio allocation than an information-rich signal.

That does not make the strategy clean or foolproof. It just reduces some obvious noise.

## Why I Like This Setup

What I like about this project is that it is opinionated without being mysterious.

There is no "trust me, the model figured it out" layer hiding in the middle. The signal sources are visible. The scoring logic is visible. The filters are visible. The model's job is visible. The dashboard shows the outcome.

That makes it a much better experiment than a black-box trading system.

If it works, I can point to where the edge probably came from:

- Cluster insider buying
- Large ownership increases
- Fresh disclosures
- Overlap between insider and congressional signals
- Sensible portfolio construction on top of those signals

If it does not work, the failure is also inspectable. Maybe the disclosures are too delayed. Maybe the market prices them in too quickly. Maybe politician disclosures add more noise than value. Maybe the strategy needs better exit logic.

That is still useful information.

## The Limitations

There are several reasons to be careful with the results.

First, this is a demo-account system. It is executing against real prices, but it is not risking real capital.

Second, the corporate insider source is OpenInsider, not the SEC directly. That is fine for the current project, but it is still an intermediary source and should be described honestly.

Third, congressional disclosures can be badly lagged. A filing can be public today even though the underlying trade happened weeks earlier.

Fourth, the strategy is still mostly a buy-side experiment. The repo has sell recommendation support, but the system thesis is really about the quality of the buy signal, not a fully developed long-short or active exit framework.

## Why I Built It

I built this because I wanted a transparent way to test whether public insider-style disclosures can be turned into a repeatable workflow.

Not by manually checking websites every few days.

Not by reading a bunch of headlines and convincing myself after the fact.

But by running the same pipeline every time:

- fetch
- score
- merge
- enrich
- decide
- execute
- track

That is the real value of the bot. It turns an interesting investing idea into something measurable.

If you want to see the output, the project includes a live dashboard with the current portfolio, recent picks, and performance versus the S&P 100:

[Live dashboard](https://thecr7guy2.github.io/agent-trading/)

Whether the strategy ultimately has durable edge is still an open question. But the system now describes exactly what it is doing, and that is the standard I wanted the code, README, and blog to meet.
