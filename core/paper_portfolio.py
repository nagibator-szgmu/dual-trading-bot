import json
import os
import time
from typing import Dict, List, Any

PORTFOLIO_FILE = "portfolio_state.json"

class PaperPortfolio:
    def __init__(self, polymarket_balance: float = 100.0, crypto_balance: float = 100.0):
        self.polymarket_balance = polymarket_balance
        self.crypto_balance = crypto_balance
        self.initial_poly = polymarket_balance
        self.initial_crypto = crypto_balance
        
        self.poly_positions: Dict[str, Any] = {}
        self.crypto_positions: Dict[str, Any] = {}
        self.trade_history: List[Dict[str, Any]] = []
        
        self.load()

    def load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.polymarket_balance = data.get("polymarket_balance", self.polymarket_balance)
                    self.crypto_balance = data.get("crypto_balance", self.crypto_balance)
                    self.poly_positions = data.get("poly_positions", {})
                    self.crypto_positions = data.get("crypto_positions", {})
                    self.trade_history = data.get("trade_history", [])
            except Exception:
                pass

    def save(self):
        data = {
            "polymarket_balance": self.polymarket_balance,
            "crypto_balance": self.crypto_balance,
            "poly_positions": self.poly_positions,
            "crypto_positions": self.crypto_positions,
            "trade_history": self.trade_history[-100:]  # keep last 100
        }
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def record_poly_trade(self, market_title: str, outcome: str, price: float, amount_usdc: float, shares: float, strategy: str):
        if self.polymarket_balance < amount_usdc:
            return False
        
        self.polymarket_balance -= amount_usdc
        trade_id = f"poly_{int(time.time()*1000)}"
        self.poly_positions[trade_id] = {
            "id": trade_id,
            "market": market_title,
            "outcome": outcome,
            "entry_price": price,
            "cost": amount_usdc,
            "shares": shares,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append({
            "type": "POLYMARKET",
            "action": "BUY",
            "market": market_title,
            "outcome": outcome,
            "price": price,
            "amount": amount_usdc,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.save()
        return trade_id

    def record_crypto_trade(self, symbol: str, side: str, price: float, amount_usdc: float, stop_loss: float, take_profit: float, strategy: str):
        if self.crypto_balance < amount_usdc:
            return False
            
        self.crypto_balance -= amount_usdc
        trade_id = f"crypto_{int(time.time()*1000)}"
        self.crypto_positions[trade_id] = {
            "id": trade_id,
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "amount": amount_usdc,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append({
            "type": "CRYPTO",
            "action": side,
            "symbol": symbol,
            "price": price,
            "amount": amount_usdc,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.save()
        return trade_id

    def close_crypto_trade(self, trade_id: str, exit_price: float, reason: str):
        if trade_id not in self.crypto_positions:
            return None
        pos = self.crypto_positions.pop(trade_id)
        side = pos["side"]
        entry_price = pos["entry_price"]
        amount = pos["amount"]
        
        # calculate pnl
        if side == "BUY":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
            
        pnl = amount * pnl_pct
        return_amount = amount + pnl
        self.crypto_balance += return_amount
        
        record = {
            "type": "CRYPTO_CLOSE",
            "trade_id": trade_id,
            "symbol": pos["symbol"],
            "side": side,
            "entry": entry_price,
            "exit": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct * 100,
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append(record)
        self.save()
        return record

    def get_stats(self) -> Dict[str, Any]:
        poly_pnl = self.polymarket_balance - self.initial_poly
        crypto_pnl = self.crypto_balance - self.initial_crypto
        total_pnl = poly_pnl + crypto_pnl
        return {
            "poly_balance": round(self.polymarket_balance, 2),
            "crypto_balance": round(self.crypto_balance, 2),
            "total_balance": round(self.polymarket_balance + self.crypto_balance, 2),
            "poly_open": len(self.poly_positions),
            "crypto_open": len(self.crypto_positions),
            "poly_pnl": round(poly_pnl, 2),
            "crypto_pnl": round(crypto_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": len(self.trade_history)
        }
