import os, sys, json, time, csv, random
import requests
from datetime import datetime, timedelta
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
from openai import OpenAI
from tavily import TavilyClient

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
DRY_RUN = True
MIN_VOLUME = 10000.0
MAX_SPREAD = 0.12
POSITION_SIZE = 10.0
TAKE_PROFIT = 0.15
STOP_LOSS = 0.20
EDGE_THRESHOLD = 0.15
MARKET_COOLDOWN_HOURS = 6          # cooldown after a HOLD decision
POST_EXIT_COOLDOWN_HOURS = 24      # cooldown after ANY closed position (TP or SL)
CONFLICT_SIMILARITY_THRESHOLD = 0.6

BLACKLIST_KEYWORDS = [
    "gta", "jesus", "christ", "rihanna", "carti", "taiwan",
    "nba", "nfl", "fifa", "world cup", "stanley cup", "aliens",
    "swift", "mrbeast", "drake", "election", "president", "biden",
    "up or down", "spread:", "o/u", "temperature", "itf", "vs.",
    "cubs", "rockies", "royals", "rangers", "astros", "angels",
    "brewers", "athletics", "sparks", "storm", "knicks", "spurs"
]

TARGET_SECTORS = {
    "macro": ["fed", "rate", "inflation", "cpi", "recession", "interest",
              "powell", "fomc", "gdp", "nfp", "unemployment", "ecb"],
    "tech":  ["openai", "gpt", "apple", "nvidia", "spacex", "anthropic",
              "ai", "meta", "bytedance", "mistral", "discord", "ipo",
              "perplexity", "kraken", "opensea"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "etf", "sec",
               "solana", "airdrop", "hyperliquid", "megaeth"]
}

# Common stop-words that shouldn't count towards market similarity
STOP_WORDS = {
    "will", "the", "a", "an", "in", "on", "at", "by", "of", "to",
    "for", "and", "or", "is", "be", "before", "after", "than",
    "2026", "2027", "2028", "market", "cap", "price", "than",
    "reach", "hit", "close"
}

CSV_FILE = "paper_trades.csv"
COOLDOWN_FILE = "market_cooldowns.json"

# ==========================================
# CLIENTS
# ==========================================
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
tavily_client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))
clob_client = ClobClient(
    "https://clob.polymarket.com",
    key=os.environ.get("POLYMARKET_PRIVATE_KEY"),
    chain_id=137, signature_type=0
)

# ==========================================
# COOLDOWN TRACKER
# ==========================================
def load_cooldowns():
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    try:
        with open(COOLDOWN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_cooldowns(cooldowns):
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(cooldowns, f)

def is_on_cooldown(market_question, cooldowns):
    if market_question not in cooldowns:
        return False
    cooldown_until = datetime.fromisoformat(cooldowns[market_question])
    return datetime.now() < cooldown_until

def set_cooldown(market_question, cooldowns, hours=MARKET_COOLDOWN_HOURS):
    cooldown_until = datetime.now() + timedelta(hours=hours)
    cooldowns[market_question] = cooldown_until.isoformat()

# ==========================================
# FUZZY MARKET CONFLICT DETECTION
# ==========================================
def normalise_question(question):
    words = question.lower().replace("?", "").replace(",", "").split()
    return set(w for w in words if w not in STOP_WORDS and len(w) > 2)

def markets_conflict(question_a, question_b):
    """True if two market questions are likely about the same underlying event."""
    if question_a == question_b:
        return True
    words_a = normalise_question(question_a)
    words_b = normalise_question(question_b)
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    similarity = len(overlap) / min(len(words_a), len(words_b))
    return similarity > CONFLICT_SIMILARITY_THRESHOLD

def has_conflicting_position(question, portfolio):
    """Check open positions for a market covering the same underlying question."""
    for held_market in portfolio.keys():
        if markets_conflict(question, held_market):
            return held_market
    return None

def has_conflicting_cooldown(question, cooldowns):
    """Check cooldowns (recently closed/held) for the same underlying question."""
    for cooled_market in cooldowns.keys():
        if is_on_cooldown(cooled_market, cooldowns) and markets_conflict(question, cooled_market):
            return cooled_market
    return None

# ==========================================
# PORTFOLIO MANAGER
# ==========================================
def load_portfolio():
    portfolio = {}
    if not os.path.exists(CSV_FILE):
        return portfolio

    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            market = row['Market']
            action = row['Action']
            size = float(row['Size'])
            price = float(row['Execution Price'])

            if market not in portfolio:
                portfolio[market] = {
                    'yes_shares': 0.0, 'yes_spent': 0.0,
                    'no_shares':  0.0, 'no_spent':  0.0,
                    'realized_pnl': 0.0
                }

            p = portfolio[market]
            if action == 'BUY_YES':
                p['yes_shares'] += size
                p['yes_spent']  += size * price
            elif action == 'SELL_YES':
                if p['yes_shares'] > 0:
                    avg = p['yes_spent'] / p['yes_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['yes_spent']  -= size * avg
                    p['yes_shares'] -= size
            elif action == 'BUY_NO':
                p['no_shares'] += size
                p['no_spent']  += size * price
            elif action == 'SELL_NO':
                if p['no_shares'] > 0:
                    avg = p['no_spent'] / p['no_shares']
                    p['realized_pnl'] += size * (price - avg)
                    p['no_spent']  -= size * avg
                    p['no_shares'] -= size

    return {
        m: d for m, d in portfolio.items()
        if d['yes_shares'] > 0.01 or d['no_shares'] > 0.01
    }

def log_trade(market, action, size, price, routing, ai_prob, reasoning):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "Timestamp", "Market", "Action", "Size",
                "Routing", "Execution Price", "AI Probability", "Reasoning"
            ])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            market, action, size, routing, price, ai_prob, reasoning
        ])
    print(f"  💾 Logged: {action} {size} @ ${price}")

# ==========================================
# PORTFOLIO AUDIT — TP / SL, now with post-exit cooldown
# ==========================================
def audit_open_positions(portfolio, markets_cache, cooldowns):
    print("\n💼 AUDITING OPEN POSITIONS...")

    market_lookup = {m.get('question'): m for m in markets_cache}

    for market_q, pos in list(portfolio.items()):
        market_data = market_lookup.get(market_q)
        if not market_data:
            print(f"  ⚠️  Cannot find live data for: {market_q[:50]}")
            continue

        try:
            raw_ids = market_data.get("clobTokenIds", "[]")
            token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
            if not token_ids:
                continue

            buy_data  = clob_client.get_price(token_ids[0], side="BUY")
            sell_data = clob_client.get_price(token_ids[0], side="SELL")
            best_ask  = float(buy_data.get('price', 1.0))
            best_bid  = float(sell_data.get('price', 0.0))
            mid_yes   = round((best_bid + best_ask) / 2, 3)
            mid_no    = round(1.0 - mid_yes, 3)
            time.sleep(0.2)

        except Exception as e:
            print(f"  ⚠️  Price fetch error for {market_q[:40]}: {e}")
            continue

        exited = False

        # YES position check
        if pos['yes_shares'] > 0.01:
            avg_entry = pos['yes_spent'] / pos['yes_shares']
            roi = (mid_yes - avg_entry) / avg_entry
            print(f"  [YES] {market_q[:45]} | Entry ${avg_entry:.3f} | Now ${mid_yes:.3f} | ROI {roi*100:+.1f}%")

            if roi >= TAKE_PROFIT:
                print(f"  🚀 TAKE PROFIT — selling YES at ${mid_yes}")
                log_trade(market_q, 'SELL_YES', pos['yes_shares'], mid_yes,
                         'EXIT (Take Profit)', 'N/A', f'TP at {roi*100:.1f}% ROI')
                exited = True
            elif roi <= -STOP_LOSS:
                print(f"  🛑 STOP LOSS — selling YES at ${mid_yes}")
                log_trade(market_q, 'SELL_YES', pos['yes_shares'], mid_yes,
                         'EXIT (Stop Loss)', 'N/A', f'SL at {roi*100:.1f}% ROI')
                exited = True

        # NO position check
        if pos['no_shares'] > 0.01:
            avg_entry = pos['no_spent'] / pos['no_shares']
            roi = (mid_no - avg_entry) / avg_entry
            print(f"  [NO]  {market_q[:45]} | Entry ${avg_entry:.3f} | Now ${mid_no:.3f} | ROI {roi*100:+.1f}%")

            if roi >= TAKE_PROFIT:
                print(f"  🚀 TAKE PROFIT — selling NO at ${mid_no}")
                log_trade(market_q, 'SELL_NO', pos['no_shares'], mid_no,
                         'EXIT (Take Profit)', 'N/A', f'TP at {roi*100:.1f}% ROI')
                exited = True
            elif roi <= -STOP_LOSS:
                print(f"  🛑 STOP LOSS — selling NO at ${mid_no}")
                log_trade(market_q, 'SELL_NO', pos['no_shares'], mid_no,
                         'EXIT (Stop Loss)', 'N/A', f'SL at {roi*100:.1f}% ROI')
                exited = True

        # KEY FIX: any closed position — TP or SL — gets a cooldown
        # so the bot can't immediately re-enter the same or a conflicting market.
        if exited:
            set_cooldown(market_q, cooldowns, hours=POST_EXIT_COOLDOWN_HOURS)
            print(f"  ⏱️  Cooldown set for {POST_EXIT_COOLDOWN_HOURS}h on: {market_q[:45]}")

# ==========================================
# MARKET SCANNER
# ==========================================
def fetch_markets():
    markets = []
    start_page = random.randint(0, 10)  # rotate which slice of the market list we see
    for i in range(5):
        offset = (start_page + i) * 100
        url = f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset={offset}"
        try:
            resp = requests.get(url, timeout=10).json()
            if isinstance(resp, list):
                markets.extend(resp)
            time.sleep(0.2)
        except Exception as e:
            print(f"  ⚠️  Page {i} fetch error: {e}")
    print(f"  📡 Fetched {len(markets)} markets total (starting page {start_page})")
    return markets

def filter_markets(markets, portfolio, cooldowns):
    viable = []

    for market in markets:
        title = market.get("question", "").lower()
        question = market.get("question", "")

        if not question:
            continue

        # Skip if we hold this market OR anything that conflicts with it
        conflict = has_conflicting_position(question, portfolio)
        if conflict:
            continue

        # Skip if this market or a conflicting one is on cooldown
        if is_on_cooldown(question, cooldowns):
            continue
        if has_conflicting_cooldown(question, cooldowns):
            continue

        if any(bl in title for bl in BLACKLIST_KEYWORDS):
            continue

        if not any(kw in title for kws in TARGET_SECTORS.values() for kw in kws):
            continue

        volume = float(market.get("volume", 0.0))
        if volume < MIN_VOLUME:
            continue

        viable.append(market)

    return viable

# ==========================================
# LLM ANALYSIS
# ==========================================
def analyse_market(question, current_price):
    current_date = datetime.now().strftime("%B %d, %Y")

    try:
        search = tavily_client.search(
            query=f"{question} latest news {current_date}",
            search_depth="advanced",
            max_results=4
        )
        news_str = "\n".join([f"- {r['content']}" for r in search.get('results', [])])
    except Exception as e:
        print(f"  ⚠️  Tavily error: {e}")
        news_str = "No news available."

    prompt = f"""You are a quantitative prediction market analyst.
Today's date: {current_date}
Market question: {question}
Current YES price: ${current_price:.3f} (implies {current_price*100:.1f}% probability)

Recent news:
{news_str}

Instructions:
1. Use ONLY the news above. Ignore any betting odds references.
2. Estimate the true probability of YES resolving.
3. If your estimate exceeds the market by 15%+ -> BUY_YES
4. If your estimate is 15%+ below the market -> BUY_NO
5. Otherwise -> HOLD

Return ONLY valid JSON:
{{
  "reasoning": "cite specific facts from the news",
  "true_probability": <float 0.0 to 1.0>,
  "action": "BUY_YES" | "BUY_NO" | "HOLD"
}}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"  ⚠️  GPT-4o error: {e}")
        return None

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    print("\n" + "="*55)
    print("🤖 QUANT BOT — STARTING RUN")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*55)

    portfolio  = load_portfolio()
    cooldowns  = load_cooldowns()
    print(f"\n📂 Open positions: {len(portfolio)}")
    print(f"⏱️  Markets on cooldown: {len(cooldowns)}")

    print("\n📡 Fetching live markets...")
    all_markets = fetch_markets()

    if portfolio:
        audit_open_positions(portfolio, all_markets, cooldowns)
        save_cooldowns(cooldowns)          # persist any post-exit cooldowns immediately
        portfolio = load_portfolio()       # reload after any exits

    print("\n🔍 SCANNING FOR NEW OPPORTUNITIES...")
    candidates = filter_markets(all_markets, portfolio, cooldowns)
    print(f"  ✅ {len(candidates)} candidates passed filters")

    if not candidates:
        print("  🛑 No viable candidates. Exiting.")
        save_cooldowns(cooldowns)
        return

    candidates.sort(key=lambda x: float(x.get("volume", 0)), reverse=True)
    top_candidates = candidates[:20]
    targets = random.sample(top_candidates, min(5, len(top_candidates)))

    for market in targets:
        question = market.get("question")
        raw_ids  = market.get("clobTokenIds", "[]")
        token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids

        if not token_ids or len(token_ids) < 2:
            continue

        # Double-check: re-verify no conflict has appeared since filtering
        # (e.g. a position was just opened earlier in this same run)
        if has_conflicting_position(question, portfolio):
            continue

        try:
            buy_data  = clob_client.get_price(token_ids[0], side="BUY")
            sell_data = clob_client.get_price(token_ids[0], side="SELL")
            best_ask  = float(buy_data.get('price', 1.0))
            best_bid  = float(sell_data.get('price', 0.0))
            spread    = best_ask - best_bid
            mid_price = round((best_bid + best_ask) / 2, 3)
            time.sleep(0.15)
        except Exception as e:
            print(f"  ⚠️  Price error: {e}")
            continue

        if mid_price < 0.10 or mid_price > 0.90:
            print(f"  [X] {question[:50]} — price {mid_price} too extreme")
            continue
        if spread > MAX_SPREAD:
            print(f"  [X] {question[:50]} — spread {spread:.3f} too wide")
            continue

        print(f"\n{'='*55}")
        print(f"🎯 ANALYSING: {question}")
        print(f"   Vol: ${float(market.get('volume',0)):,.0f} | Mid: ${mid_price} | Spread: {spread:.3f}")

        decision = analyse_market(question, mid_price)
        if not decision:
            continue

        action      = decision.get('action', 'HOLD')
        true_prob   = float(decision.get('true_probability', mid_price))
        reasoning   = decision.get('reasoning', '')
        market_prob = mid_price if action == 'BUY_YES' else (1.0 - mid_price)
        edge        = abs(true_prob - market_prob)

        print(f"  🤖 {action} | Model: {true_prob*100:.1f}% | Market: {mid_price*100:.1f}% | Edge: {edge*100:.1f}%")

        if action == 'HOLD':
            set_cooldown(question, cooldowns)
            print(f"  ⏸️  HOLD — market on {MARKET_COOLDOWN_HOURS}hr cooldown")
            continue

        if action in ('BUY_YES', 'BUY_NO'):
            if action == 'BUY_YES':
                exec_price = best_ask if edge > 0.20 else round(best_bid + 0.001, 3)
                token_id   = token_ids[0]
                routing    = 'TAKER' if edge > 0.20 else 'MAKER'
            else:
                exec_price = round(1.0 - best_bid, 3) if edge > 0.20 else round((1.0 - best_ask) + 0.001, 3)
                token_id   = token_ids[1]
                routing    = 'TAKER' if edge > 0.20 else 'MAKER'

            if DRY_RUN:
                log_trade(question, action, POSITION_SIZE, exec_price,
                         routing, f"{true_prob*100:.1f}%", reasoning)
                # Reflect the new position locally so later markets in this
                # same run can't open a conflicting trade against it.
                portfolio[question] = portfolio.get(question, {
                    'yes_shares': 0.0, 'yes_spent': 0.0,
                    'no_shares': 0.0, 'no_spent': 0.0, 'realized_pnl': 0.0
                })
                if action == 'BUY_YES':
                    portfolio[question]['yes_shares'] += POSITION_SIZE
                    portfolio[question]['yes_spent']  += POSITION_SIZE * exec_price
                else:
                    portfolio[question]['no_shares'] += POSITION_SIZE
                    portfolio[question]['no_spent']  += POSITION_SIZE * exec_price
            else:
                try:
                    creds = clob_client.create_or_derive_api_creds()
                    clob_client.set_api_creds(creds)
                    order = clob_client.create_order(
                        OrderArgs(price=exec_price, size=POSITION_SIZE, side=BUY, token_id=token_id)
                    )
                    clob_client.post_order(order, OrderType.GTC)
                    log_trade(question, action, POSITION_SIZE, exec_price,
                             routing, f"{true_prob*100:.1f}%", reasoning)
                except Exception as e:
                    print(f"  ❌ Order failed: {e}")

        time.sleep(15)

    save_cooldowns(cooldowns)
    print(f"\n✅ Run complete — {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
