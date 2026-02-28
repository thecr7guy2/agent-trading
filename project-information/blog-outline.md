# Blog Post: I gave Claude Opus €5,000/week in play money and let it read SEC filings for me

*How I built a fully autonomous insider trading signal bot with a two-stage LLM pipeline, a custom conviction scorer, and zero human approval gates.*

---

## Section 1 — The Hook

Every Tuesday and Friday at 5:10pm Berlin time, a Python script wakes up on an old laptop sitting in my Amsterdam apartment — repurposed as a home server running Ubuntu Server, reachable from anywhere via Tailscale — scrapes a public SEC filing database for stocks where company executives have recently bought shares with their own money, runs those candidates through a two-stage LLM pipeline, and places real-ish trades on a Trading 212 demo account — all without a single human looking at it first. The budget is €1,000 per run. No approval screen. No "are you sure?" No human in the loop at all.

I've been running this experiment for a few weeks now as part of a deliberate 10-week test: give the bot a €5,000/week deployment and just watch what happens. The account cap is €500,000 in practice money. The thesis isn't to get rich — it's to find out whether a small, well-structured LLM pipeline can systematically identify stocks worth holding, then check back in 6–12 months to see how the portfolio actually performs.

The core signal driving everything is **insider buying**. When a CEO or CFO buys their own company's stock, they file a Form 4 with the SEC within two days. It's public information. It's legal to trade on. And unlike analyst upgrades or Reddit hype, it represents someone with genuine skin in the game — and access to information no outsider has — deciding to put their own money in.

One cluster buy is interesting. Multiple executives at the same company buying in the same week is a different kind of signal. That's what the bot hunts for.

On February 22nd, the bot found 19 insider candidates. It bought five stocks — KKR, CNDT, HTGC, WY, and DKNG — spending the full €1,000 in under 30 seconds. Claude's stated confidence for the run: 72%. You can see the live dashboard tracking every position here: **[DASHBOARD_URL]**

Let me walk you through exactly how it works.

---

<!-- SECTIONS 2–7 TO BE DRAFTED -->
