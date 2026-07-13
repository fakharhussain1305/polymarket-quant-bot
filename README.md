# polymarket-bot
# Polymarket Quant Bot

An automated prediction market trading system built in Python, running on GitHub Actions. The bot scans [Polymarket](https://polymarket.com) for mispriced markets, uses GPT-4o and live news search to estimate true probabilities, and manages a paper-trading portfolio with automated entry, take-profit, and stop-loss logic.

Built as a portfolio project while studying for an MSc in Aerospace Engineering, as part of a career transition into quantitative finance.

---

## How It Works

The bot runs on a scheduled GitHub Actions workflow and follows three stages each cycle:

**1. Audit open positions**
Before scanning for new trades, the bot prices every currently held position directly via Polymarket's CLOB API using cached token IDs — so audits are not dependent on which markets happen to appear in that run's scan. Positions that have crossed the take-profit (+15%) or stop-loss (-20%) threshold are closed and logged. A 24-hour cooldown is set on any closed market to prevent immediate re-entry.

**2. Scan for candidates**
500 markets are fetched from the Gamma API. Each is filtered against a sector whitelist (macro, tech, crypto), minimum volume ($10,000), maximum spread (12 cents), price range (10–90 cents), and a blacklist of sports, celebrity, and unactionable markets. Markets on cooldown, or fuzzy-matched as similar to a market already held in portfolio, are skipped.

**3. Analyse and trade**
Up to 5 filtered candidates are passed to GPT-4o with live news context from Tavily Search. The model returns a true probability estimate and a BUY_YES / BUY_NO / HOLD decision. If the estimated edge exceeds 15%, a $10 paper position is opened. HOLDs set a 6-hour cooldown on that market.

---

## Architecture

```
main.py                  Core bot logic (audit → scan → analyse → trade)
paper_trades.csv         Persistent trade ledger (committed back to repo each run)
market_cooldowns.json    Per-market cooldown timestamps (persisted between runs)
position_tokens.json     CLOB token ID cache for held positions (persisted between runs)
.github/workflows/
  run_bot.yml            Scheduled GitHub Actions workflow
```

All three JSON/CSV state files are committed back to the repo at the end of each run so that state persists across the ephemeral GitHub Actions environment.

---

## Key Design Decisions

**Fuzzy conflict detection** — Before opening a position, the bot checks whether any currently held market covers the same underlying question. This uses word-overlap similarity after stripping stop-words, so "OpenAI IPO before 2027?" and "Will OpenAI go public by December 31 2026?" are correctly identified as the same bet, preventing the portfolio from taking contradictory positions on the same event.

**Token ID caching** — Each market's CLOB token IDs are stored at buy-time in `position_tokens.json`. This means the audit function can always price a held position directly, without relying on that market appearing in the day's random 500-market scan — which previously caused positions to silently go unaudited for extended periods.

**State persistence** — The GitHub Actions workflow commits all three state files (`paper_trades.csv`, `market_cooldowns.json`, `position_tokens.json`) back to the repo after each run. Committing only the CSV — as was initially done — caused cooldowns and the token cache to silently reset on every run.

**Price sanity guard** — Before acting on any audit decision, bid and ask prices are validated: both must be strictly between 0 and 1, bid must be below ask, and spread must be under 50 cents. Implausible prices are logged and skipped rather than triggering a false exit.

**Bid/ask orientation** — Polymarket's CLOB API returns the best resting buy order (bid) when called with `side="BUY"`, and the best resting sell order (ask) with `side="SELL"`. Variable names in the code reflect the correct orientation; swapping these produces a systematically negative spread and silently disables the spread filter.

---

## Bugs Found and Fixed

This project has been as much an exercise in systematic debugging as in strategy design. Bugs found through log analysis and their fixes:

| Bug | Effect | Fix |
|---|---|---|
| Swapped `best_bid` / `best_ask` assignment | Spread always negative; spread filter never fired; entry/exit prices anchored to wrong side of book | Corrected variable assignment from CLOB API response |
| `log_trade()` call outside the TP/SL `if` block | Every position closed on every audit run regardless of ROI | Moved sell logic inside threshold guard |
| State files not committed to repo | Cooldowns and token cache reset to zero on every run | Workflow now commits all three state files |
| Audit used random market scan to look up token IDs | Positions not in that day's sample went unaudited indefinitely | Token IDs cached at buy-time; audit prices directly via CLOB |
| Exact-string conflict check | Bot held BUY_YES and BUY_NO on same underlying event when question wording differed slightly | Replaced with fuzzy word-overlap similarity check |

---

## Paper Trading Results (as of July 2026)

| Metric | Value |
|---|---|
| Total trades logged | 157 |
| Unique markets | 40 |
| Closed positions | 19 |
| Open positions | 21 |
| **Realized P&L** | **+$7.52** |
| Capital at risk (open) | $102.50 |
| Take-profit exits | 7 |
| Stop-loss exits | 67 |

The realized P&L figure is dominated by two outsized wins — MegaETH airdrop (+$6.63) and Fed rate cuts (+$3.12) — and partially offset by losses on IPO markets (OpenAI, Applied Intuition, Kraken, Discord, Freddie Mac) where GPT-4o systematically overestimated the probability of near-term IPOs. The high stop-loss count relative to take-profits (67 vs 7) reflects the bugs documented above: the majority of pre-July-3rd exits were caused by the `log_trade` indentation bug and do not reflect real strategy performance. Post-fix performance (July 3rd onwards) shows a single stop-loss and zero take-profits on a still-small sample.

---

## Stack

- **Python 3.11**
- [py-clob-client](https://github.com/Polymarket/py-clob-client) — Polymarket CLOB order placement and pricing
- [OpenAI Python SDK](https://github.com/openai/openai-python) — GPT-4o for probability estimation
- [Tavily](https://tavily.com) — Live news search for LLM context
- **GitHub Actions** — Scheduled execution and state persistence

---

## Running Locally

```bash
pip install -r requirements.txt

export OPENAI_API_KEY=your_key
export TAVILY_API_KEY=your_key
export POLYMARKET_PRIVATE_KEY=your_key

python main.py
```

`DRY_RUN = True` is set by default at the top of `main.py`. No real orders will be placed until this is set to `False` and a funded Polymarket wallet is connected.

---

## Disclaimer

This is a paper-trading research project. No real money is at risk. Nothing here constitutes financial advice.
