"""
analyze_portfolio.py
--------------------
Rebuilds the paper portfolio from paper_trades.csv and prints realised
performance, split by the code version that produced each trade.

Needs no API keys and makes no network calls, so it runs from a fresh clone:

    python analyze_portfolio.py

Positions are costed on an average-price basis. Sell rows that arrive when no
shares are held are skipped and counted: they were written by an early bug
(log_trade() sat outside its take-profit / stop-loss check, so a position
could be "sold" repeatedly) and are kept in the ledger as a record of it.
"""

import csv
import os
import sys
from collections import defaultdict

CSV_FILE = "paper_trades.csv"

# Dates the strategy materially changed. Results are reported per era so that
# P&L produced by a known bug is never mixed with P&L from the fixed code.
ERAS = [
    ("2026-06-14", "v1: original code (exit-logging bug)"),
    ("2026-07-03", "v2: exit and pricing bugs fixed"),
    # v3 was pushed on 24 Oct 2026 shortly before 16:00 UTC (ledger times are UTC).
    ("2026-10-24 16:00", "v3: whole-word filters, edge threshold enforced"),
]


def era_for(timestamp):
    label = ERAS[0][1]
    for start, name in ERAS:
        if timestamp >= start:
            label = name
    return label


def analyse(csv_file=CSV_FILE):
    if not os.path.exists(csv_file):
        print(f"{csv_file} not found. Run this from the repo root.")
        return None

    book = defaultdict(lambda: {"YES": [0.0, 0.0], "NO": [0.0, 0.0]})  # shares, cost
    closes = []  # (timestamp, market, pnl, exit_type)
    skipped = 0
    rows = 0

    with open(csv_file, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("Timestamp"):
                continue
            rows += 1
            action = row["Action"]
            side = "YES" if action.endswith("YES") else "NO"
            size = float(row["Size"])
            price = float(row["Execution Price"])
            pos = book[row["Market"]][side]

            if action.startswith("BUY"):
                pos[0] += size
                pos[1] += size * price
                continue

            if pos[0] <= 0.01:
                skipped += 1
                continue
            size = min(size, pos[0])
            avg = pos[1] / pos[0]
            pnl = size * (price - avg)
            pos[0] -= size
            pos[1] -= size * avg
            exit_type = "TP" if "Take Profit" in row["Routing"] else "SL"
            closes.append((row["Timestamp"], row["Market"], pnl, exit_type))

    open_positions = [
        (market, side, shares, cost)
        for market, sides in book.items()
        for side, (shares, cost) in sides.items()
        if shares > 0.01
    ]
    return {"rows": rows, "closes": closes, "open": open_positions,
            "skipped": skipped, "markets": len(book)}


def summarise(closes):
    n = len(closes)
    if n == 0:
        return "no closed trades"
    pnl = sum(c[2] for c in closes)
    wins = sum(1 for c in closes if c[2] > 0)
    tps = sum(1 for c in closes if c[3] == "TP")
    return (f"{n:>4} exits | {tps} TP / {n - tps} SL | win rate {wins / n:5.1%} | "
            f"realised ${pnl:+7.2f} | per exit ${pnl / n:+.3f}")


def main(csv_file=CSV_FILE):
    result = analyse(csv_file)
    if result is None:
        return 1

    print("=" * 78)
    print("PAPER PORTFOLIO - REALISED PERFORMANCE")
    print("=" * 78)
    print(f"Ledger rows: {result['rows']}   Markets traded: {result['markets']}")
    if result["skipped"]:
        print(f"Skipped {result['skipped']} legacy sell rows with no shares held "
              f"(early exit-logging bug, see README)")
    print()
    print(f"All trades   {summarise(result['closes'])}")
    by_era = defaultdict(list)
    for c in result["closes"]:
        by_era[era_for(c[0])].append(c)
    for _, name in ERAS:
        if by_era.get(name):
            print(f"  {name}")
            print(f"             {summarise(by_era[name])}")

    print()
    open_cost = sum(p[3] for p in result["open"])
    print(f"Open positions: {len(result['open'])}   Capital at risk: ${open_cost:.2f}")
    print("(run pnl_snapshot.py to mark them to market; it needs network access)")
    for market, side, shares, cost in sorted(result["open"], key=lambda p: -p[3]):
        print(f"  [{side:<3}] {shares:5.1f} @ ${cost / shares:.3f}  {market[:58]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
