import csv
import os

def analyze_performance():
    csv_filename = "paper_trades.csv"
    
    if not os.path.exists(csv_filename):
        print("No trade data found. Let the bot run first!")
        return

    portfolio = {}
    total_spent = 0.0
    realized_pnl = 0.0
    winning_trades = 0
    losing_trades = 0

    with open(csv_filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        
        for row in reader:
            market = row['Market']
            action = row['Action']
            size = float(row['Size'])
            price = float(row['Execution Price'])
            
            if market not in portfolio:
                portfolio[market] = {
                    'YES_shares': 0.0, 'YES_invested': 0.0,
                    'NO_shares': 0.0, 'NO_invested': 0.0,
                    'realized': 0.0
                }

            # Tally up Capital Deployed
            total_spent += (size * price) if 'BUY' in action else 0

            # Handle YES Positions
            if action == 'BUY_YES':
                portfolio[market]['YES_shares'] += size
                portfolio[market]['YES_invested'] += (size * price)
            
            elif action == 'SELL_YES':
                avg_cost = portfolio[market]['YES_invested'] / portfolio[market]['YES_shares']
                cost_basis = size * avg_cost
                revenue = size * price
                profit = revenue - cost_basis
                
                portfolio[market]['YES_shares'] -= size
                portfolio[market]['YES_invested'] -= cost_basis
                portfolio[market]['realized'] += profit
                realized_pnl += profit
                
                if profit > 0: winning_trades += 1
                else: losing_trades += 1

            # Handle NO Positions
            elif action == 'BUY_NO':
                portfolio[market]['NO_shares'] += size
                portfolio[market]['NO_invested'] += (size * price)
            
            elif action == 'SELL_NO':
                avg_cost = portfolio[market]['NO_invested'] / portfolio[market]['NO_shares']
                cost_basis = size * avg_cost
                revenue = size * price
                profit = revenue - cost_basis
                
                portfolio[market]['NO_shares'] -= size
                portfolio[market]['NO_invested'] -= cost_basis
                portfolio[market]['realized'] += profit
                realized_pnl += profit
                
                if profit > 0: winning_trades += 1
                else: losing_trades += 1

    # --- PRINT THE DASHBOARD ---
    total_closed_trades = winning_trades + losing_trades
    win_rate = (winning_trades / total_closed_trades * 100) if total_closed_trades > 0 else 0

    print("="*50)
    print("📊 QUANT BOT PERFORMANCE DASHBOARD")
    print("="*50)
    print(f"Total Capital Deployed: ${total_spent:.2f}")
    print(f"Total Closed Trades:    {total_closed_trades}")
    print(f"Win Rate:               {win_rate:.1f}%")
    print(f"Total Realized PnL:     ${realized_pnl:.2f}")
    print("-" * 50)
    print("📂 CURRENT OPEN POSITIONS:")
    
    open_positions = False
    for market, data in portfolio.items():
        if data['YES_shares'] > 0.1:
            open_positions = True
            avg_price = data['YES_invested'] / data['YES_shares']
            print(f"- [YES] {market[:30]}... | {data['YES_shares']} shares @ ${avg_price:.3f}")
        if data['NO_shares'] > 0.1:
            open_positions = True
            avg_price = data['NO_invested'] / data['NO_shares']
            print(f"- [NO]  {market[:30]}... | {data['NO_shares']} shares @ ${avg_price:.3f}")
            
    if not open_positions:
        print("- No open positions.")
    print("="*50)

if __name__ == "__main__":
    analyze_performance()
