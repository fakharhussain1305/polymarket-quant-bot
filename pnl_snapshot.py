"""
pnl_snapshot.py
---------------
Standalone script — prices every open position at current market mid-price
and prints a full P&L snapshot showing what you'd walk away with if you
closed everything right now.

Run with:
    python pnl_snapshot.py

Requires the same environment variables as main.py:
    POLYMARKET_PRIVATE_KEY
"""

import os, json, csv, time
from collections import defaultdict
from datetime import datetime
from py_clob_client.client import ClobClient

CSV_FILE        = "paper_trades.csv"
POSITION_TOKENS = "position_tokens.json"

clob_client = ClobClient(
    "https://clob.polymarket.com",
    key=os.environ.get("POLYMARKET_PRIVATE_KEY"),
    chain_id=137, signature_type=0
)

# ── 1. Rebuild portfolio from trade log ──────────────────────────────────────

def load_portfolio():
    positions = defaultdict(lambda: {
        'yes_shares': 0.0, 'yes_spent': 0.0,
        'no_shares':  0.0, 'no_spent':  0.0,
        'realized_pnl': 0.0
    })

    if not os.path.exists(CSV_FILE):
        print(f"ERROR: {CSV_FILE} not found — make sure you run this from the repo root.")
        return {}

    with open(CSV_FILE, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row['Timestamp']:
                continue
            m     = row['Market']
            act   = row['Action']
            size  = float(row['Size'])
            price = float(row['Execution Price'])
            p     = positions[m]

            if act == 'BUY_YES':
                p['yes_shares'] += size;  p['yes_spent'] += size * price
            elif act == 'SELL_YES':
                if p['yes_shares'] > 0:
                    avg = p['yes_spent'] / p['yes_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['yes_spent']    -= size * avg
                    p['yes_shares']   -= size
            elif act == 'BUY_NO':
                p['no_shares'] += size;  p['no_spent'] += size * price
            elif act == 'SELL_NO':
                if p['no_shares'] > 0:
                    avg = p['no_spent'] / p['no_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['no_spent']     -= size * avg
                    p['no_shares']    -= size

    return {
        m: d for m, d in positions.items()
        if d['yes_shares'] > 0.01 or d['no_shares'] > 0.01
    }


def load_position_tokens():
    if not os.path.exists(POSITION_TOKENS):
        return {}
    try:
        with open(POSITION_TOKENS) as f:
            return json.load(f)
    except Exception:
        return {}


# ── 2. Fetch current mid-price for a token ID ────────────────────────────────

def fetch_mid(token_id):
    """Returns (mid_price, bid, ask) or None on failure."""
    try:
        buy_data  = clob_client.get_price(token_id, side="BUY")
        sell_data = clob_client.get_price(token_id, side="SELL")

        if 'price' not in buy_data or 'price' not in sell_data:
            return None

        bid = float(buy_data['price'])
        ask = float(sell_data['price'])

        # Sanity check
        if bid <= 0 or ask <= 0 or bid >= 1 or ask >= 1 or bid > ask:
            return None

        mid = round((bid + ask) / 2, 4)
        return mid, bid, ask
    except Exception as e:
        print(f"    Price fetch error: {e}")
        return None


# ── 3. Main snapshot ─────────────────────────────────────────────────────────

def main():
    print("\n" + "="*65)
    print("  P&L SNAPSHOT —", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("="*65)

    portfolio       = load_portfolio()
    position_tokens = load_position_tokens()

    if not portfolio:
        print("No open positions found.")
        return

    total_cost        = 0.0
    total_current_val = 0.0
    total_realized    = 0.0
    rows              = []
    unpriced          = []

    for market, pos in portfolio.items():
        token_ids = position_tokens.get(market)

        if pos['yes_shares'] > 0.01:
            side   = 'YES'
            shares = pos['yes_shares']
            cost   = pos['yes_spent']
            token  = token_ids[0] if token_ids else None
        else:
            side   = 'NO'
            shares = pos['no_shares']
            cost   = pos['no_spent']
            token  = token_ids[0] if token_ids else None

        avg_entry = cost / shares
        total_cost += cost

        if token is None:
            unpriced.append((market, side, shares, avg_entry, cost))
            continue

        result = fetch_mid(token)
        time.sleep(0.15)  # be gentle with the API

        if result is None:
            unpriced.append((market, side, shares, avg_entry, cost))
            continue

        mid, bid, ask = result

        # For NO positions, the value of your shares is (1 - YES mid)
        current_price = mid if side == 'YES' else round(1.0 - mid, 4)
        current_val   = shares * current_price
        pnl           = current_val - cost
        roi_pct       = (pnl / cost) * 100 if cost > 0 else 0

        total_current_val += current_val
        rows.append({
            'market':        market,
            'side':          side,
            'shares':        shares,
            'avg_entry':     avg_entry,
            'current_price': current_price,
            'cost':          cost,
            'current_val':   current_val,
            'pnl':           pnl,
            'roi_pct':       roi_pct
        })

    # Also sum all realized P&L from the CSV (closed trades)
    all_positions_for_realized = defaultdict(lambda: {
        'yes_shares': 0.0, 'yes_spent': 0.0,
        'no_shares':  0.0, 'no_spent':  0.0,
        'realized_pnl': 0.0
    })
    with open(CSV_FILE, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row['Timestamp']:
                continue
            m     = row['Market']
            act   = row['Action']
            size  = float(row['Size'])
            price = float(row['Execution Price'])
            p     = all_positions_for_realized[m]
            if act == 'BUY_YES':
                p['yes_shares'] += size;  p['yes_spent'] += size * price
            elif act == 'SELL_YES':
                if p['yes_shares'] > 0:
                    avg = p['yes_spent'] / p['yes_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['yes_spent']    -= size * avg
                    p['yes_shares']   -= size
            elif act == 'BUY_NO':
                p['no_shares'] += size;  p['no_spent'] += size * price
            elif act == 'SELL_NO':
                if p['no_shares'] > 0:
                    avg = p['no_spent'] / p['no_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['no_spent']     -= size * avg
                    p['no_shares']    -= size
    total_realized = sum(d['realized_pnl'] for d in all_positions_for_realized.values())

    # ── Print results ────────────────────────────────────────────────────────

    # Sort by P&L descending
    rows.sort(key=lambda x: x['pnl'], reverse=True)

    print(f"\n{'Market':<52} {'Side':<4} {'Entry':>6} {'Now':>6} {'Cost':>7} {'Value':>7} {'P&L':>7} {'ROI':>7}")
    print("-"*103)

    for r in rows:
        marker = " ✅" if r['pnl'] > 0 else (" 🛑" if r['roi_pct'] <= -20 else "")
        print(
            f"  {r['market'][:50]:<50} "
            f"  {r['side']:<4} "
            f"  {r['avg_entry']:>5.3f} "
            f"  {r['current_price']:>5.3f} "
            f"  ${r['cost']:>5.2f} "
            f"  ${r['current_val']:>5.2f} "
            f"  {r['pnl']:>+6.2f} "
            f"  {r['roi_pct']:>+5.1f}%"
            f"{marker}"
        )

    print("-"*103)

    priced_cost = sum(r['cost'] for r in rows)
    unrealized_pnl = total_current_val - priced_cost

    print(f"\n{'OPEN POSITIONS (priced)':<35} Cost: ${priced_cost:>7.2f}   Value: ${total_current_val:>7.2f}   Unrealized P&L: {unrealized_pnl:>+7.2f}")

    if unpriced:
        unpriced_cost = sum(r[4] for r in unpriced)
        print(f"{'OPEN POSITIONS (unpriced)':<35} Cost: ${unpriced_cost:>7.2f}   (no token ID cached — run main.py to backfill)")
        total_cost += unpriced_cost

    print(f"{'REALIZED P&L (closed trades)':<35}                                    {total_realized:>+7.2f}")
    print("-"*65)
    net = unrealized_pnl + total_realized
    print(f"{'NET P&L (if sold everything now)':<35}                                    {net:>+7.2f}")
    print()

    if unpriced:
        print(f"⚠️  {len(unpriced)} positions could not be priced (missing token cache):")
        for m, side, shares, avg, cost in unpriced:
            print(f"   [{side}] ${avg:.3f} entry  ${cost:.2f} at risk  | {m[:55]}")

    winners = [r for r in rows if r['pnl'] > 0]
    losers  = [r for r in rows if r['pnl'] < 0]
    near_sl = [r for r in rows if r['roi_pct'] <= -15]
    print(f"\n📊 {len(rows)} positions priced  |  {len(winners)} in profit  |  {len(losers)} at a loss  |  {len(near_sl)} within 5% of stop-loss")
    print()


if __name__ == "__main__":
    main()
