import os
import sys
import json
import time
import csv
import requests
import urllib.parse
from datetime import datetime
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
from openai import OpenAI
from tavily import TavilyClient

# ==========================================
# ⚙️ BOT CONFIGURATION (Optimized)
# ==========================================
DRY_RUN = True       # Set to False to execute real trades on-chain
MIN_VOLUME = 10000.0  # Lowered to capture emerging setups early
MAX_SPREAD = 0.12     # Adjusted for dynamic liquidity constraints

# Strict filtering to discard noise, memes, and long-term locked attention bets
BLACKLIST_KEYWORDS = [
    "gta", "jesus", "christ", "rihanna", "carti", "trump", "taiwan", 
    "nba", "nfl", "fifa", "world cup", "stanley cup", "aliens", "swift",
    "mrbeast", "drake", "election", "president", "biden"
]

# Broadened target list to catch new sectors
TARGET_SECTORS = {
    "macro": ["fed", "rate", "inflation", "cpi", "recession", "interest", "powell", "fomc", "gdp", "nfp", "unemployment", "jobs report", "ecb"],
    "tech": ["openai", "gpt", "apple", "nvidia", "spacex", "sam altman", "anthropic", "ai", "meta"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "etf", "sec", "solana", "sol", "crypto", "binance", "airdrop", "gensler"],
    "science": ["boeing", "starliner", "nasa", "cern", "nuclear", "launch", "fda", "approval", "clinical"]
}
# ==========================================

# --- 1. INITIALIZE CLIENTS ---
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
tavily_client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))

client = ClobClient(
    "https://clob.polymarket.com", 
    key=os.environ.get("POLYMARKET_PRIVATE_KEY"), 
    chain_id=137, 
    signature_type=0
)

# --- 2. EXECUTION & LOGGING UTILITIES ---
def execute_paper_sell(market, size, price, reason, action_type, token_id):
    with open("paper_trades.csv", mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            market,
            action_type,  
            size,
            "MAKER (Exit Protocol)",
            price,
            token_id, # <-- Hardcodes the exact asset token ID
            reason
        ])
    print(f"💾 {action_type} Order successfully logged to ledger.")

def check_take_profit(target_market, live_yes_price):
    csv_filename = "paper_trades.csv"
    if not os.path.exists(csv_filename):
        return

    yes_shares, yes_spent = 0.0, 0.0
    no_shares, no_spent = 0.0, 0.0

    with open(csv_filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row['Market'] == target_market:
                size = float(row['Size'])
                price = float(row['Execution Price'])
                action = row['Action']
                
                if action == 'BUY_YES':
                    yes_shares += size
                    yes_spent += (size * price)
                elif action == 'SELL_YES':
                    yes_shares -= size
                    if yes_shares > 0.01: 
                        avg_cost = yes_spent / (yes_shares + size)
                        yes_spent -= (size * avg_cost)
                    else:
                        yes_shares, yes_spent = 0.0, 0.0
                elif action == 'BUY_NO':
                    no_shares += size
                    no_spent += (size * price)
                elif action == 'SELL_NO':
                    no_shares -= size
                    if no_shares > 0.01:
                        avg_cost = no_spent / (no_shares + size)
                        no_spent -= (size * avg_cost)
                    else:
                        no_shares, no_spent = 0.0, 0.0

    # YES Evaluation
    if yes_shares > 0.1:
        avg_entry_yes = yes_spent / yes_shares
        roi_yes = (live_yes_price - avg_entry_yes) / avg_entry_yes
        print(f"💼 OPEN POSITION [YES]: Holding {yes_shares} shares | Avg Entry: ${avg_entry_yes:.3f} | Live Price: ${live_yes_price:.3f} | ROI: {roi_yes*100:.1f}%")
        
        if roi_yes >= 0.15:
            print("🚀 TAKE PROFIT TRIGGERED! Locking in 15%+ gains on YES.")
            execute_paper_sell(target_market, yes_shares, live_yes_price, f"Take Profit triggered at {roi_yes*100:.1f}% ROI", "SELL_YES", "N/A")
        elif roi_yes <= -0.20:
            print("🛑 STOP LOSS TRIGGERED! Cutting losses at -20% on YES.")
            execute_paper_sell(target_market, yes_shares, live_yes_price, f"Stop Loss triggered at {roi_yes*100:.1f}% ROI", "SELL_YES", "N/A")

    # NO Evaluation
    if no_shares > 0.1:
        avg_entry_no = no_spent / no_shares
        live_no_price = round(1.0 - live_yes_price, 3) 
        roi_no = (live_no_price - avg_entry_no) / avg_entry_no
        print(f"💼 OPEN POSITION [NO]: Holding {no_shares} shares | Avg Entry: ${avg_entry_no:.3f} | Live Price: ${live_no_price:.3f} | ROI: {roi_no*100:.1f}%")
        
        if roi_no >= 0.15:
            print("🚀 TAKE PROFIT TRIGGERED! Locking in gains on NO.")
            execute_paper_sell(target_market, no_shares, live_no_price, f"Take Profit triggered at {roi_no*100:.1f}% ROI", "SELL_NO")
        elif roi_no <= -0.20:
            print("🛑 STOP LOSS TRIGGERED! Cutting losses on NO.")
            execute_paper_sell(target_market, no_shares, live_no_price, f"Stop Loss triggered at {roi_no*100:.1f}% ROI", "SELL_NO")

# --- 3. DECOUPLED PORTFOLIO ENGINE ---
def manage_open_positions():
    print("\n💼 WAKING UP PORTFOLIO MANAGER (TOKEN ID PROTOCOL)...")
    csv_filename = "paper_trades.csv"
    if not os.path.exists(csv_filename):
        print("  - No active positions to track.")
        return

    portfolio = {}
    
    # 1. Read ledger and group strictly by the unique asset
    with open(csv_filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            market = row['Market']
            action = row['Action']
            size = float(row['Size'])
            price = float(row['Execution Price'])
            
            if market not in portfolio:
                portfolio[market] = {'yes_shares': 0.0, 'yes_spent': 0.0, 'no_shares': 0.0, 'no_spent': 0.0}
                
            if action == 'BUY_YES':
                portfolio[market]['yes_shares'] += size
                portfolio[market]['yes_spent'] += (size * price)
            elif action == 'SELL_YES':
                portfolio[market]['yes_shares'] -= size
            elif action == 'BUY_NO':
                portfolio[market]['no_shares'] += size
                portfolio[market]['no_spent'] += (size * price)
            elif action == 'SELL_NO':
                portfolio[market]['no_shares'] -= size

    # 2. Direct Order Book Call (Bypasses text query ambiguity)
    for market, data in portfolio.items():
        if data['yes_shares'] > 0.1 or data['no_shares'] > 0.1:
            print(f"📡 Auditing Asset book for: '{market[:40]}...'")
            try:
                # Pull the full live 500 options from Gamma to isolate the EXACT market object
                url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=500"
                markets = requests.get(url).json()
                
                for target_market_data in markets:
                    if target_market_data.get("question") == market:
                        raw_ids = target_market_data.get("clobTokenIds", "[]")
                        token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                        
                        # Fetch the direct cryptographic orderbook price
                        buy_data = client.get_price(token_ids[0], side="BUY")
                        sell_data = client.get_price(token_ids[0], side="SELL")
                        best_ask = float(buy_data.get('price', 1.0))
                        best_bid = float(sell_data.get('price', 0.0))
                        live_yes_price = round((best_bid + best_ask) / 2, 3)
                        
                        # Calmly check the real take profit math
                        check_take_profit(market, live_yes_price)
                        break
            except Exception as e:
                print(f"  - Risk validation error: {e}")

# Run the account audit immediately!
manage_open_positions()

# --- 4. MULTI-CATEGORY BATCH SCREENER ---
print("\n📡 Scanning Polymarket for serious Macro & Tech opportunities...")

markets = []
try:
    # THE CRAWLER: Fetch 5 pages of 100 markets each (500 Total Markets)
    for i in range(5):
        offset = i * 100
        url = f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset={offset}"
        response = requests.get(url).json()
        
        # If the API returns a list, add it to our master dataset
        if isinstance(response, list):
            markets.extend(response)
        
        # Micro-pause so Polymarket doesn't ban our IP for spamming requests
        time.sleep(0.2)
        
    print(f"  - Successfully downloaded {len(markets)} active markets from orderbook.")
except Exception as e:
    print(f"❌ Failed to connect to Gamma API: {e}")
    sys.exit()

viable_opportunities = []
# ... (The rest of your `for market in markets:` loop stays exactly the same!)
viable_opportunities = []

for market in markets:
    title = market.get("question", "").lower()
    volume = float(market.get("volume", 0.0))

    if volume < MIN_VOLUME: continue
    if any(bl in title for bl in BLACKLIST_KEYWORDS): continue
    if not any(kw in title for keywords in TARGET_SECTORS.values() for kw in keywords): continue

    raw_token_ids = market.get("clobTokenIds", "[]")
    token_ids = json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else raw_token_ids
    if not token_ids or len(token_ids) < 2: continue

    yes_token_id = token_ids[0]
    no_token_id = token_ids[1]

    try:
        time.sleep(0.15) 
        buy_data = client.get_price(yes_token_id, side="BUY")
        sell_data = client.get_price(yes_token_id, side="SELL")

        best_ask = float(buy_data.get('price', 1.0))
        best_bid = float(sell_data.get('price', 0.0))
        spread = best_ask - best_bid
        current_price = round((best_bid + best_ask) / 2, 3)

        # X-Ray Diagnostic Prints
        if current_price < 0.10 or current_price > 0.90:
            print(f"  [X] Skipped '{market.get('question')[:40]}...' (Price {current_price} too extreme)")
            continue
        if spread > MAX_SPREAD:
            print(f"  [X] Skipped '{market.get('question')[:40]}...' (Spread {spread:.3f} too wide)")
            continue

        viable_opportunities.append({
            "question": market.get("question"),
            "current_price": current_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "volume": volume,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id
        })
    except: 
        continue

if not viable_opportunities:
    print("🛑 No alternative active markets met the strict safety criteria on this check.")
    sys.exit()

# Sort opportunities and target the Top 5
viable_opportunities.sort(key=lambda x: x['volume'], reverse=True)
top_targets = viable_opportunities[:5]

print(f"\n🔄 Filtered {len(viable_opportunities)} viable options. Parsing the Top {len(top_targets)}...")

# --- 5. THE MASTER LOOP ---
for target in top_targets:
    market_question = target['question']
    current_price = target['current_price']
    best_bid = target['best_bid']
    best_ask = target['best_ask']
    yes_token_id = target['yes_token_id']
    no_token_id = target['no_token_id']

    print(f"\n" + "="*50)
    print(f"🚀 TARGET LOCKED: '{market_question}'")
    print(f"📊 Volume: ${target['volume']:,.2f} | 💰 Midpoint Price: ${current_price}")
    print("="*50)

    try:
        current_date = datetime.now().strftime("%B %d, %Y")
        print(f"\n🔎 Querying real-time intelligence feeds...")

        try:
            search_result = tavily_client.search(
                query=f"Latest official news {current_date} update: {market_question}", 
                search_depth="advanced", 
                max_results=4 
            )
            news_str = "\n".join([f"- {r['content']}" for r in search_result.get('results', [])])
            print("📰 CONTEXT ACQUIRED (Preview):")
            print(news_str[:250] + "...\n")
        except Exception as e:
            print(f"⚠️ Search down: {e}. Executing fallback models.")
            news_str = "No recent data scraped."

        # Brain Prompt Logic
        prompt = f"""
        You are a ruthless, quantitative trading algorithm analyzing a prediction market. 
        CRITICAL CONTEXT: Today's exact date is {current_date}. 
        Market Question: {market_question}
        Current Market Price for 'Yes': ${current_price} (Implies a {float(current_price)*100}% probability)

        Here is the scraped news data:
        {news_str}

        Task & Constraints:
        1. FACT-CHECK: Discard any outdated information relative to today's date ({current_date}).
        2. NO EXTERNAL ODDS: You are FORBIDDEN from citing betting odds.
        3. FUNDAMENTAL ANALYSIS: Calculate probability based ONLY on hard facts.
        4. If your probability is at least 15% HIGHER than the market, output "BUY_YES".
        5. If your probability is at least 15% LOWER than the market, output "BUY_NO".
        6. Otherwise, output "HOLD".

        Return ONLY raw, valid JSON:
        {{
            "step_by_step_reasoning": "Explicitly cite facts.",
            "true_probability": 0.0 to 1.0,
            "action": "BUY_YES", "BUY_NO", or "HOLD"
        }}
        """

        response = openai_client.chat.completions.create(
            model="gpt-4o", 
            messages=[{"role": "user", "content": prompt}], 
            response_format={"type": "json_object"},
            temperature=0.0 
        )
        ai_decision = json.loads(response.choices[0].message.content)

        print(f"🤖 EVALUATION: {ai_decision['action']} | Model Fair Value: {ai_decision['true_probability']*100}%")
        print(f"Reasoning: {ai_decision['step_by_step_reasoning']}")

        if ai_decision['action'] in ["BUY_YES", "BUY_NO"]:
            trade_size = 10.0 

            market_prob = float(current_price) if ai_decision['action'] == "BUY_YES" else (1.0 - float(current_price))
            true_prob = float(ai_decision['true_probability'])
            edge_delta = abs(true_prob - market_prob)

            if ai_decision['action'] == "BUY_YES":
                target_token = yes_token_id
                taker_price = best_ask  
                maker_price = round(best_bid + 0.001, 3) 
            else:
                target_token = no_token_id
                taker_price = round(1.0 - best_bid, 3) 
                maker_price = round((1.0 - best_ask) + 0.001, 3)

            if edge_delta > 0.15:
                execution_style = "TAKER (High Urgency)"
                execution_price = taker_price
            else:
                execution_style = "MAKER (Low Urgency)"
                execution_price = maker_price

            if DRY_RUN:
                print(f"\n📝 RECORDING PAPER POSITION: {execution_style} at ${execution_price}")
                csv_filename = "paper_trades.csv"
                file_exists = os.path.isfile(csv_filename)

                with open(csv_filename, mode='a', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    if not file_exists:
                        writer.writerow(["Timestamp", "Market", "Action", "Size", "Routing", "Execution Price", "AI Probability", "Reasoning"])
                    writer.writerow([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        market_question,
                        ai_decision['action'],
                        trade_size,
                        execution_style,
                        execution_price,
                        f"{float(ai_decision['true_probability'])*100:.1f}%",
                        ai_decision['step_by_step_reasoning']
                    ])
            else:
                print(f"\n⚡ TRANSMITTING ON-CHAIN ORDER...")
                api_creds = client.create_or_derive_api_creds()
                client.set_api_creds(api_creds)
                order_args = OrderArgs(price=execution_price, size=trade_size, side=BUY, token_id=target_token)
                signed_order = client.create_order(order_args)
                client.post_order(signed_order, OrderType.GTC)
        else:
            print("\n🛑 STATUS HOLD: Market mispricing gap insufficient for deployment entry.")

        print("⏳ Rate pacing: Sleeping 15s to clear request quotas...")
        time.sleep(15)

    except Exception as e:
        print(f"⚠️ Pipeline exception on current iteration: {e}")
        continue

print("\n✅ System workflow iteration completed successfully.")
