"""Unit tests for the bot's filtering and decision logic.

No API keys or network access needed:  python -m pytest
Several cases are real market titles the bot traded by mistake before the
whole-word matching fix.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from analyze_portfolio import analyse  # noqa: E402


# --- Keyword matching ------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Ukraine signs peace deal with Russia before 2027?",            # ai in Ukraine
    "Pedro Sánchez out as PM of Spain by December 31, 2026?",        # ai in Spain
    "Will the Democrats win the Maine Senate race in 2026?",         # ai in Maine
    "Will Kuwait join the Abraham Accords before 2027?",             # ai in Kuwait
    "Will Seth Moulton be the Democratic nominee for Senate in Massachusetts?",  # eth
    "Will The Netherlands recognize Palestine before 2027?",          # eth
    "Will 2026 be the second-hottest year on record?",               # sec
    "Will Trump cut corporate taxes before 2027?",                   # rate
])
def test_previously_leaked_markets_are_now_excluded(title):
    passes = (main.is_in_target_sector(title)
              and not main.contains_any(title, main.BLACKLIST_KEYWORDS))
    assert not passes


@pytest.mark.parametrize("title", [
    "AI bubble burst in 2026?",
    "OpenAI IPO before 2027?",
    "Will Ethereum reach $4,000 by December 31, 2026?",
    "Will ETH reach $5,000?",
    "Will 1 Fed rate cut happen in 2026?",
    "US recession by end of 2026?",
    "Will Ethena reach $0.32 by December 31, 2026?",
])
def test_intended_markets_still_pass(title):
    assert main.is_in_target_sector(title)
    assert not main.contains_any(title, main.BLACKLIST_KEYWORDS)


def test_punctuated_keywords_still_match():
    assert main.contains_keyword("lakers vs. celtics", "vs.")
    assert main.contains_keyword("points o/u 210.5", "o/u")


# --- Trade decision ---------------------------------------------------------

@pytest.mark.parametrize("prob, mid, label, expected", [
    (0.85, 0.12, "BUY_YES", "BUY_YES"),   # large YES edge, label agrees
    (0.10, 0.60, "BUY_NO",  "BUY_NO"),    # large NO edge, label agrees
    (0.25, 0.148, "BUY_YES", "HOLD"),     # 10% edge: below threshold (real ledger case)
    (0.85, 0.12, "BUY_NO",  "HOLD"),      # label contradicts its own probability (real case)
    (0.50, 0.50, "HOLD",    "HOLD"),
    (0.80, 0.50, "HOLD",    "HOLD"),      # model saw edge but chose HOLD: respect it
])
def test_decide_trade(prob, mid, label, expected):
    action, _ = main.decide_trade(prob, mid, label)
    assert action == expected


def test_buy_no_edge_compares_like_with_like():
    # Old code: edge = |P(YES) - NO price| = |0.40 - 0.40| = 0 for this case.
    _, edge = main.decide_trade(0.40, 0.60, "BUY_NO")
    assert edge == pytest.approx(-0.20)


# --- Duplicate-market detection ---------------------------------------------

def test_conflict_detection_matches_rewordings():
    assert main.markets_conflict("Will OpenAI IPO by December 31 2026?",
                                 "OpenAI IPO before 2027?")


def test_conflict_detection_separates_different_events():
    assert not main.markets_conflict("Will Bitcoin reach $100,000 by December 31, 2026?",
                                     "Will Solana dip to $20 by December 31, 2026?")


# --- Ledger replay ------------------------------------------------------------

def test_ledger_replay_survives_legacy_oversells(tmp_path):
    ledger = tmp_path / "trades.csv"
    ledger.write_text(
        "Timestamp,Market,Action,Size,Routing,Execution Price,AI Probability,Reasoning\n"
        "2026-06-14 01:00:00,M,BUY_NO,10,MAKER,0.40,30%,x\n"
        "2026-06-14 02:00:00,M,SELL_NO,10,EXIT (Take Profit),0.50,N/A,x\n"
        "2026-06-14 03:00:00,M,SELL_NO,10,EXIT (Stop Loss),0.30,N/A,x\n",
        encoding="utf-8")
    result = analyse(str(ledger))
    assert result["skipped"] == 1
    assert [round(c[2], 6) for c in result["closes"]] == [1.0]
    assert result["open"] == []
