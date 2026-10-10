import json
import os
import time
from typing import Dict, List, Any, Optional

PORTFOLIO_FILE = "portfolio_state.json"

class CryptoPortfolio:
    def __init__(self, starting_balance: float = 100.0):
        self.cash_balance = starting_balance
        self.initial_balance = starting_balance
        self.positions: Dict[str, Any] = {}
        self.trade_history: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.cash_balance = data.get("cash_balance", self.cash_balance)
                    self.initial_balance = data.get("initial_balance", self.initial_balance)
                    self.positions = data.get("positions", {})
                    self.trade_history = data.get("trade_history", [])
            except Exception:
                pass

    def save(self):
        temp_file = f"{PORTFOLIO_FILE}.tmp"
        data = {
            "cash_balance": self.cash_balance,
            "initial_balance": self.initial_balance,
            "positions": self.positions,
            "trade_history": self.trade_history[-100:]
        }
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, PORTFOLIO_FILE)
        except Exception:
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def open_position(self, symbol: str, side: str, price: float, amount_usd: float, stop_loss: float, take_profit: float, strategy: str):
        if self.cash_balance < amount_usd:
            return None
            
        self.cash_balance -= amount_usd
        trade_id = f"pos_{symbol}_{int(time.time()*1000)}"
        self.positions[trade_id] = {
            "id": trade_id,
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "amount": amount_usd,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "open_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append({
            "action": f"OPEN_{side}",
            "symbol": symbol,
            "price": price,
            "amount": amount_usd,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.save()
        return trade_id

    def close_position(self, trade_id: str, exit_price: float, reason: str):
        if trade_id not in self.positions:
            return None
            
        pos = self.positions.pop(trade_id)
        side = pos["side"]
        entry_price = pos["entry_price"]
        amount = pos["amount"]
        
        # Calculate real PnL
        if side == "BUY":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
            
        pnl_usd = amount * pnl_pct
        return_amount = max(0.0, amount + pnl_usd)
        self.cash_balance += return_amount
        
        record = {
            "action": f"CLOSE_{side}",
            "trade_id": trade_id,
            "symbol": pos["symbol"],
            "side": side,
            "entry": entry_price,
            "exit": exit_price,
            "pnl": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append(record)
        self.save()
        return record

    def get_stats(self, live_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        live_prices = live_prices or {}
        
        cost_in_positions = sum(p["amount"] for p in self.positions.values())
        current_positions_value = 0.0
        
        detailed_positions = []
        for pid, pos in self.positions.items():
            sym = pos["symbol"]
            side = pos["side"]
            amt = pos["amount"]
            entry = pos["entry_price"]
            
            cur_px = live_prices.get(sym, entry)
            if side == "BUY":
                ratio = (cur_px - entry) / entry
            else:
                ratio = (entry - cur_px) / entry
                
            pnl_usd = amt * ratio
            val = max(0.0, amt + pnl_usd)
            current_positions_value += val
            
            detailed_positions.append({
                "id": pid,
                "symbol": sym,
                "side": side,
                "entry": entry,
                "current": cur_px,
                "stop_loss": pos["stop_loss"],
                "take_profit": pos["take_profit"],
                "pnl": round(pnl_usd, 2),
                "pnl_pct": round(ratio * 100, 2)
            })

        floating_pnl = current_positions_value - cost_in_positions
        total_equity = self.cash_balance + current_positions_value
        total_pnl = total_equity - self.initial_balance
        pnl_pct_total = (total_pnl / self.initial_balance) * 100 if self.initial_balance > 0 else 0

        # Win rate calculation from trade history
        closed_trades = [t for t in self.trade_history if "CLOSE" in t.get("action", "")]
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

        return {
            "cash": round(self.cash_balance, 2),
            "invested": round(cost_in_positions, 2),
            "equity": round(total_equity, 2),
            "floating_pnl": round(floating_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct_total, 2),
            "open_count": len(self.positions),
            "closed_count": len(closed_trades),
            "win_rate": round(win_rate, 1),
            "positions": detailed_positions
        }
