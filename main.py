import os
import sys
import json
import time
import csv
import requests
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
MIN_VOLUME = 10000.0  # Lowered slightly to capture emerging high-value tech/macro setups
MAX_SPREAD = 0.12     # Maximum allowed gap between buy/sell price

# Strict filtering to discard noise, memes, and long-term locked attention bets
BLACKLIST_KEYWORDS = [
    "gta", "jesus", "christ", "rihanna", "carti", "trump", "taiwan", 
    "nba", "nfl", "fifa", "world cup", "stanley cup", "aliens", "swift",
    "mrbeast", "drake", "election", "president", "biden"
]

# Broadened core tokens to catch variations like "MegaETH" or "Wrapped BTC"
TARGET_SECTORS = {
    "macro": ["fed", "rate", "inflation", "cpi", "recession", "interest", "powell", "fomc", "gdp", "nfp", "unemployment", "jobs report", "ecb", "sec", "gensler"],
    "tech": ["openai", "gpt", "apple", "nvidia", "spacex", "sam altman", "anthropic", "ai", "meta"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "etf", "sec", "solana", "sol", "crypto", "binance", "airdrop"],
    "bio-pharma": ["fda", "approval", "clinical trial", "phase 3", "vaccine", "cdc"],
    "science and space": ["boeing", "starliner", "nasa", "cern", "nuclear", "fusion", "launch"]
   
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

# --- 2. PORTFOLIO MANAGER FUNCTIONS ---
def execute_paper_sell(market, size, price, reason, action_type):
    with open("paper_trades.csv", mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            market,
            action_type,  
            size,
            "MAKER (Exit Protocol)",
            price,
            "N/A", 
            reason
        ])
    print(f"💾 {action_type} Order successfully logged to ledger.")

def check_take_profit(target_market, live_yes_price):
    csv_filename = "paper_trades.csv"
    if not os.path.exists(csv_filename):
        return

    yes_shares, yes_spent = 0.0, 0.0
    no_shares, no_spent = 0.0, 0.0

    # Read the ledger to calculate our YES and NO positions
    with open(csv_filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row['Market'] == target_market:
                size = float(row['Size'])
                price = float(row['Execution Price'])
                
                if row['Action'] == 'BUY_YES':
                    yes_shares += size
                    yes_spent += (size * price)
                elif row['Action'] == 'SELL_YES':
                    yes_shares -= size
                    if yes_shares > 0.01: 
                        avg_cost = yes_spent / (yes_shares + size)
                        yes_spent -= (size * avg_cost)
                    else:
                        yes_shares, yes_spent = 0.0, 0.0
                        
                elif row['Action'] == 'BUY_NO':
                    no_shares += size
                    no_spent += (size * price)
                elif row['Action'] == 'SELL_NO':
                    no_shares -= size
                    if no_shares > 0.01:
                        avg_cost = no_spent / (no_shares + size)
                        no_spent -= (size * avg_cost)
                    else:
                        no_shares, no_spent = 0.0, 0.0

    # Check and Execute YES Positions
    if yes_shares > 0.1:
        avg_entry_yes = yes_spent / yes_shares
        roi_yes = (live_yes_price - avg_entry_yes) / avg_entry_yes
        print(f"💼 PORTFOLIO [YES]: Holding {yes_shares} shares | Avg Entry: ${avg_entry_yes:.3f} | Live Price: ${live_yes_price:.3f} | ROI: {roi_yes*100:.1f}%")
        
        if roi_yes >= 0.15:
            print("🚀 TAKE PROFIT TRIGGERED! Locking in 15%+ gains on YES.")
            execute_paper_sell(target_market, yes_shares, live_yes_price, f"Take Profit triggered at {roi_yes*100:.1f}% ROI", "SELL_YES")
        elif roi_yes <= -0.20:
            print("🛑 STOP LOSS TRIGGERED! Cutting losses at -20% on YES.")
            execute_paper_sell(target_market, yes_shares, live_yes_price, f"Stop Loss triggered at {roi_yes*100:.1f}% ROI", "SELL_YES")

    # Check and Execute NO Positions
    if no_shares > 0.1:
        avg_entry_no = no_spent / no_shares
        live_no_price = 1.0 - live_yes_price 
        roi_no = (live_no_price - avg_entry_no) / avg_entry_no
        print(f"💼 PORTFOLIO [NO]: Holding {no_shares} shares | Avg Entry: ${avg_entry_no:.3f} | Live Price: ${live_no_price:.3f} | ROI: {roi_no*100:.1f}%")
        
        if roi_no >= 0.15:
            print("🚀 TAKE PROFIT TRIGGERED! Locking in gains on NO.")
            execute_paper_sell(target_market, no_shares, live_no_price, f"Take Profit triggered at {roi_no*100:.1f}% ROI", "SELL_NO")
        elif roi_no <= -0.20:
            print("🛑 STOP LOSS TRIGGERED! Cutting losses on NO.")
            execute_paper_sell(target_market, no_shares, live_no_price, f"Stop Loss triggered at {roi_no*100:.1f}% ROI", "SELL_NO")


# --- 3. MULTI-CATEGORY BATCH SCREENER ---
print("📡 Scanning Polymarket for serious Macro & Tech opportunities...")
try:
    markets = requests.get("https://gamma-api.polymarket.com/markets?active=true&closed=false").json()
except Exception as e:
    print(f"❌ Failed to connect to Gamma API: {e}")
    sys.exit()

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

        if spread > MAX_SPREAD or current_price < 0.10 or current_price > 0.90:
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
    print("🛑 No serious markets met the strict safety criteria right now.")
    sys.exit()

# Sort opportunities by volume and take the TOP 5
viable_opportunities.sort(key=lambda x: x['volume'], reverse=True)
top_targets = viable_opportunities[:5]

print(f"\n🔄 Found {len(viable_opportunities)} viable markets. Analyzing the Top {len(top_targets)}...")

# --- 4. THE MASTER LOOP ---
for target in top_targets:
    market_question = target['question']
    current_price = target['current_price']
    best_bid = target['best_bid']
    best_ask = target['best_ask']
    yes_token_id = target['yes_token_id']
    no_token_id = target['no_token_id']

    print(f"\n" + "="*50)
    print(f"🚀 TARGET LOCKED: '{market_question}'")
    print(f"📊 Volume: ${target['volume']:,.2f} | 💰 Midpoint: ${current_price}")
    print("="*50)

    try:
        # 1. Run Portfolio Manager First
        check_take_profit(market_question, current_price)

        # 2. RAG Pipeline
        current_date = datetime.now().strftime("%B %d, %Y")
        print(f"\n🔎 Gathering real-time insights (Anchored to {current_date})...")

        try:
            search_result = tavily_client.search(
                query=f"Latest official news {current_date} update: {market_question}", 
                search_depth="advanced", 
                max_results=4 
            )
            news_str = "\n".join([f"- {r['content']}" for r in search_result.get('results', [])])
            print("📰 RAW NEWS SCRAPED (Truncated):")
            print(news_str[:300] + "...\n")
        except Exception as e:
            print(f"⚠️ Search warning: {e}. Analyzing with baseline values.")
            news_str = "No recent data scraped."

        # 3. LLM Decision Engine
        prompt = f"""
        You are a ruthless, quantitative trading algorithm analyzing a prediction market. 

        CRITICAL CONTEXT: Today's exact date is {current_date}. 

        Market Question: {market_question}
        Current Market Price for 'Yes': ${current_price} (Implies a {float(current_price)*100}% probability)

        Here is the scraped news data:
        {news_str}

        Task & Constraints:
        1. FACT-CHECK: Discard any outdated information relative to today's date ({current_date}).
        2. NO EXTERNAL ODDS: You are FORBIDDEN from citing other prediction markets, betting sites, or "odds" mentioned in the news. 
        3. FUNDAMENTAL ANALYSIS: You must calculate your probability based ONLY on hard facts.
        4. Calculate the true structural probability (0.0 to 1.0) based on your fundamental analysis.
        5. If your probability is at least 15% HIGHER than the market, output "BUY_YES".
        6. If your probability is at least 15% LOWER than the market, output "BUY_NO".
        7. Otherwise, output "HOLD".

        CRITICAL: Return ONLY raw, valid JSON.
        Format:
        {{
            "step_by_step_reasoning": "Explicitly cite the fundamental facts from the text you used.",
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

        print(f"🤖 SIGNAL: {ai_decision['action']} | Calculated Probability: {ai_decision['true_probability']*100}%")
        print(f"Reasoning: {ai_decision['step_by_step_reasoning']}")

        # 4. Hybrid Smart Order Execution
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
                reason = f"Massive {edge_delta*100:.1f}% edge detected. Paying spread to guarantee fill."
            else:
                execution_style = "MAKER (Low Urgency)"
                execution_price = maker_price
                reason = f"Thin {edge_delta*100:.1f}% edge. Limit order placed."

            if DRY_RUN:
                print("\n" + "-"*50)
                print("📝 HYBRID VIRTUAL TRADE RECEIPT")
                print(f"Action: {ai_decision['action']} | Size: {trade_size} Shares")
                print(f"Routing: {execution_style} | Target Price: ${execution_price}")
                print("-"*50)

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
                print(f"💾 Trade successfully logged to {csv_filename}")
            else:
                print(f"\n⚡ ROUTING LIVE {execution_style} ORDER...")
                try:
                    api_creds = client.create_or_derive_api_creds()
                    client.set_api_creds(api_creds)
                    order_args = OrderArgs(price=execution_price, size=trade_size, side=BUY, token_id=target_token)
                    signed_order = client.create_order(order_args)
                    resp = client.post_order(signed_order, OrderType.GTC)
                    print(f"✅ {execution_style} TRADE PLACED: {resp}")
                except Exception as e:
                    print(f"❌ EXECUTION FAILURE: {e}")
        else:
            print("\n🛑 NO TRADE: Midpoint consensus matches calculated probabilities.")

        # 5. API Rate Limit Protection (CRUCIAL)
        print("⏳ Waiting 15 seconds before scanning next market to protect API limits...")
        time.sleep(15)

    except Exception as e:
        print(f"⚠️ Error processing {market_question}: {e}. Skipping to next...")
        continue

print("\n✅ Hourly Top 5 scan complete. Server shutting down until next cycle.")
