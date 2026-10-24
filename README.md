# Polymarket Quant Bot

An automated paper-trading system that asks whether a large language model, given live news, can price prediction-market contracts better than the market does. It has run unattended on GitHub Actions since June 2026 and logged every decision to a public ledger.

**Short answer so far: no.** After the early bugs were fixed, 242 closed trades won 48% of the time and lost $13.08 on $10 positions, about 5p per trade. The breakdown below shows where the losses came from. Finding that out, and building the tooling to be sure the loss was real rather than a bug, is what this project is about.

Built as a portfolio project alongside an MSc in Aerospace Engineering, as part of a move into quantitative finance. Paper trading only: no real money has been placed.

---

## Background: prediction markets in one paragraph

On [Polymarket](https://polymarket.com), each question ("Will the Fed cut rates in 2026?") has a YES share and a NO share. A winning share pays $1 and a losing share pays nothing, so a YES price of $0.30 is the market saying "30% likely". If you believe the true probability is 50%, buying at $0.30 has positive expected value. The bot's whole strategy rests on that comparison: an estimated probability against the market price.

---

## How it works

Each scheduled run has three stages.

**1. Audit open positions.** Every held position is priced directly from Polymarket's order book (the CLOB API), using token IDs cached when the position was opened. A position at +15% closes as a take-profit and one at −20% as a stop-loss. A closed market then cannot be re-entered for 24 hours.

**2. Scan for candidates.** 500 active markets are fetched from the Gamma API and filtered:
- the title must contain a macro, tech or crypto keyword and no blacklisted keyword, using whole-word matching (see the bugs table);
- volume must be at least $10,000, the spread at most 12¢ and the price between 10¢ and 90¢;
- the market must not be on cooldown or a reworded duplicate of one already held.

**3. Analyse and trade.** Five of the top candidates go to GPT-4o along with recent news from Tavily search. The model returns a probability that YES resolves, an action and its reasoning. A $10 paper position opens only if the model's action agrees with its own probability *and* that probability is at least 15 percentage points from the market price. Otherwise the market is put on a 6-hour cooldown.

```
main.py                 Bot: audit -> scan -> analyse -> trade
analyze_portfolio.py    Replays the ledger: realised P&L by code version (no keys, no network)
pnl_snapshot.py         Marks open positions to market (needs network access)
tests/                  Unit tests for filtering, edge logic and ledger replay
paper_trades.csv        Every trade with the model's probability and reasoning
market_cooldowns.json   Cooldown state, persisted between runs
position_tokens.json    Token-ID cache for held positions
.github/workflows/      Scheduled bot run, and tests on every push
```

GitHub Actions machines start fresh every run, so the workflow commits the three state files back to the repo afterwards. That is why most commits here are automated.

---

## Results (as of 24 October 2026)

Regenerate these with `python analyze_portfolio.py`.

| Code version | Exits | Take-profit / stop-loss | Win rate | Realised P&L | Per exit |
|---|---|---|---|---|---|
| v1: 14 Jun – 2 Jul, exit-logging bug live | 47 | 7 / 40 | 59.6% | +$7.94 | +$0.169 |
| **v2: 3 Jul – 24 Oct, bugs fixed** | **242** | **117 / 125** | **48.3%** | **−$13.08** | **−$0.054** |
| All | 290 | 124 / 166 | 50.0% | −$5.90 | −$0.020 |

The ledger has 654 rows across 93 markets. 34 positions are open, with $279.40 of capital at risk. v1's profit is not evidence of skill. An indentation bug closed positions on every run whatever the price, so v1's exits are arbitrary.

### Why v2 lost money

**The exit rule needs a 57% win rate to break even.** If winners make about +15% and losers about −20%, the break-even win rate is 20 ÷ (15 + 20) ≈ 57%. The bot won 48%. With these thresholds, the model's probability estimates had to be clearly better than the market's just to stand still.

**A quarter of the markets were never meant to be traded.** Keyword matching used plain substrings, so `"ai"` matched Ukr*ai*ne, Sp*ai*n, M*ai*ne and Kuw*ai*t, `"eth"` matched S*eth* and N*eth*erlands, `"sec"` matched *sec*ond-hottest and `"rate"` matched corpo*rate*. 23 of 93 markets were politics or weather. The single most-traded market, bought 15 times, was "Will 2026 be the second-hottest year on record?". The leaked markets were not what lost the money, though: in v2 they roughly broke even (+$1.32 over 45 exits). The losses came from the markets the bot was designed to trade.

**The model's own edge test was never applied.** `EDGE_THRESHOLD` was defined but never checked, so the bot acted on the model's BUY/HOLD label alone. By the model's own probabilities, 173 of 333 buys had less than 15 points of edge. Five bought the side the model itself thought less likely. In one case it bought NO on "Ukraine agrees not to join NATO" while estimating 85% for YES.

**Fixing both would not have produced an edge.** Replaying v2 with the whole-word filter and edge check applied, 102 exits would still have been taken. They won 44% of the time and lost about 6p each, roughly the same as the trades the fixes would have removed. This replay is approximate, because entry price stands in for the market mid at the time. Both fixes are now live as v3, because the bot should do what its design says. However, the evidence points at the signal itself: **GPT-4o reading four news snippets does not out-forecast a liquid prediction market.**

**By market type (v2 exits):**

| Category | Exits | Win rate | Realised P&L |
|---|---|---|---|
| IPO markets | 49 | 31% | −$9.53 |
| Other tech | 71 | 54% | −$6.16 |
| Crypto | 51 | 47% | −$4.00 |
| Leaked (politics, weather) | 45 | 53% | +$1.32 |
| Macro | 27 | 59% | +$4.53 |

IPO markets account for most of the loss. The model repeatedly overestimated the chance of a company listing before 2027. A plausible reason is that news coverage of an IPO being *prepared* reads to the model as evidence that it will *happen*, while the market prices in how often listings slip. Macro is the only profitable category, but 27 trades is far too few to call that an edge.

### Why the paper results flatter the strategy

- Exits are priced at the mid-price, but a real sell would hit the bid, which is lower.
- Maker orders are assumed to fill instantly, when in reality they may never fill.
- No fees, gas or slippage are modelled.

A live version would do worse than these numbers.

---

## Bugs found and fixed

Most were found by reading the ledger and the run logs, not by the strategy failing loudly. Most of them failed silently.

| Bug | Effect | Fix |
|---|---|---|
| `log_trade()` called outside the take-profit / stop-loss `if` block | Every position closed on every audit run, regardless of price | Moved the sell inside the threshold check (v2) |
| Bid and ask variables swapped | Spread always negative, so the spread filter never fired | Corrected the CLOB response mapping (v2) |
| Only the trade CSV committed between runs | Cooldowns and the token cache reset every hour | Workflow commits all three state files (v2) |
| Audit found token IDs through the random 500-market scan | Positions not in that run's sample went unaudited for days | Token IDs cached at buy time (v2) |
| Exact-string duplicate check | Held YES and NO on the same event when the wording differed | Word-overlap similarity check (v2) |
| Substring keyword matching | 23 of 93 traded markets were outside the target sectors | Whole-word matching, regression tests on the real titles (v3) |
| `EDGE_THRESHOLD` never checked; BUY_NO edge compared P(YES) with the NO price | Trades taken with little or contradictory edge | `decide_trade()` enforces the threshold in the right direction (v3) |
| Ledger replay divided by zero on sells with no position | `analyze_portfolio.py` crashed on a fresh clone | Orphan sells skipped and reported (v3) |

The 31 orphan sell rows from the first bug stay in the ledger. It is an honest record of what the bot did, not a curated one.

---

## Design notes

- **Duplicate detection.** "OpenAI IPO before 2027?" and "Will OpenAI IPO by December 31 2026?" are the same bet. Questions are compared by word overlap after removing stop-words, so the bot cannot hold contradictory positions on one event.
- **Price sanity checks.** An audit is skipped, not acted on, if the bid or ask is missing, outside (0, 1), crossed, or more than 50¢ apart. An earlier version silently defaulted a failed price fetch to $1.00 or $0.00, which can fake a huge ROI swing.
- **Clients created at run time, not import time.** The filtering and decision logic can therefore be imported and tested without API keys.

---

## Running it

Without any API keys, from a fresh clone:

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q            # filtering, edge and ledger-replay tests
python analyze_portfolio.py    # regenerates the results table above
```

To run the bot itself, you need OpenAI and Tavily keys:

```bash
export OPENAI_API_KEY=...
export TAVILY_API_KEY=...
export POLYMARKET_PRIVATE_KEY=...   # wallet key; only used if DRY_RUN is False
python main.py
```

`DRY_RUN = True` is set at the top of `main.py`. In that mode trades are written to the CSV and no orders are sent. The workflow is scheduled hourly, but GitHub runs scheduled jobs on public repos less often than that in practice.

---

## How this was built

I designed the strategy, and the first version of the code was written with Google Gemini. From there I moved to Claude, using it as a pair programmer for the debugging work behind most of the bugs table: I pasted run logs and ledger rows, we worked out which behaviour was a bug and which was the market, and I made and tested the changes. The v3 audit was done with Claude Code working directly on this repo. It reproduced the results from the ledger, found the substring-matching and unchecked-edge bugs, wrote the regression tests and replayed the ledger to check whether the fixes would have changed the conclusion.

The judgement calls were mine. I chose what to measure, which results to trust, and to publish the losing numbers rather than tune the bot until they looked better.

---

## Stack

Python 3.11 · [py-clob-client](https://github.com/Polymarket/py-clob-client) · [OpenAI Python SDK](https://github.com/openai/openai-python) (GPT-4o) · [Tavily](https://tavily.com) · GitHub Actions · pytest

## Disclaimer

A paper-trading research project. No real money is at risk, and nothing here is financial advice.
