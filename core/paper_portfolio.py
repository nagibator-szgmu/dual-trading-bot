import json
import os
import time
from typing import Dict, List, Any, Optional

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
        # Atomic file write to avoid corrupted JSON on sudden crash/power loss
        temp_file = f"{PORTFOLIO_FILE}.tmp"
        data = {
            "polymarket_balance": self.polymarket_balance,
            "crypto_balance": self.crypto_balance,
            "poly_positions": self.poly_positions,
            "crypto_positions": self.crypto_positions,
            "trade_history": self.trade_history[-100:]
        }
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, PORTFOLIO_FILE)
        except Exception:
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def record_poly_trade(self, market_title: str, outcome: str, price: float, amount_usdc: float, shares: float, strategy: str, slug: str = ""):
        if self.polymarket_balance < amount_usdc:
            return False
        
        self.polymarket_balance -= amount_usdc
        trade_id = f"poly_{int(time.time()*1000)}"
        self.poly_positions[trade_id] = {
            "id": trade_id,
            "market": market_title,
            "slug": slug,
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
            "slug": slug,
            "outcome": outcome,
            "price": price,
            "amount": amount_usdc,
            "strategy": strategy,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.save()
        return trade_id

    def settle_poly_trade(self, trade_id: str, won: bool, reason: str = ""):
        if trade_id not in self.poly_positions:
            return None
        pos = self.poly_positions.pop(trade_id)
        cost = pos["cost"]
        shares = pos["shares"]
        
        if won:
            # Each winning share redeems for $1.00 USDC
            return_amount = shares * 1.00
            pnl = return_amount - cost
        else:
            # Losing share is worth $0.00
            return_amount = 0.0
            pnl = -cost
            
        self.polymarket_balance += return_amount
        record = {
            "type": "POLYMARKET_SETTLE",
            "trade_id": trade_id,
            "market": pos["market"],
            "slug": pos.get("slug", ""),
            "outcome": pos["outcome"],
            "cost": cost,
            "return_amount": return_amount,
            "pnl": round(pnl, 2),
            "won": won,
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append(record)
        self.save()
        return record

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
        return_amount = max(0.0, amount + pnl)
        self.crypto_balance += return_amount
        
        record = {
            "type": "CRYPTO_CLOSE",
            "trade_id": trade_id,
            "symbol": pos["symbol"],
            "side": side,
            "entry": entry_price,
            "exit": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trade_history.append(record)
        self.save()
        return record

    def get_stats(self, crypto_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        crypto_prices = crypto_prices or {}
        
        # 1. Poly open positions current valuation
        poly_cost_total = sum(pos.get("cost", 0) for pos in self.poly_positions.values())
        poly_current_value = poly_cost_total  # baseline until resolved or priced
        poly_floating_pnl = poly_current_value - poly_cost_total
        
        poly_equity = self.polymarket_balance + poly_current_value
        poly_total_pnl = poly_equity - self.initial_poly

        # 2. Crypto live Mark-to-Market valuation
        crypto_cost_total = sum(pos.get("amount", 0) for pos in self.crypto_positions.values())
        crypto_current_value = 0.0
        
        crypto_pos_details = []
        for pid, pos in self.crypto_positions.items():
            sym = pos["symbol"]
            side = pos["side"]
            amt = pos["amount"]
            entry = pos["entry_price"]
            
            cur_px = crypto_prices.get(sym, entry)
            if side == "BUY":
                ratio = (cur_px - entry) / entry
            else:
                ratio = (entry - cur_px) / entry
                
            pos_pnl = amt * ratio
            pos_val = max(0.0, amt + pos_pnl)
            crypto_current_value += pos_val
            
            crypto_pos_details.append({
                "id": pid,
                "symbol": sym,
                "side": side,
                "entry": entry,
                "current": cur_px,
                "pnl": round(pos_pnl, 2),
                "pnl_pct": round(ratio * 100, 2)
            })

        crypto_floating_pnl = crypto_current_value - crypto_cost_total
        crypto_equity = self.crypto_balance + crypto_current_value
        crypto_total_pnl = crypto_equity - self.initial_crypto

        total_cash = self.polymarket_balance + self.crypto_balance
        total_assets = poly_current_value + crypto_current_value
        total_equity = total_cash + total_assets
        total_pnl = total_equity - (self.initial_poly + self.initial_crypto)

        return {
            "poly_cash": round(self.polymarket_balance, 2),
            "poly_invested": round(poly_cost_total, 2),
            "poly_equity": round(poly_equity, 2),
            "poly_pnl": round(poly_total_pnl, 2),
            "crypto_cash": round(self.crypto_balance, 2),
            "crypto_invested": round(crypto_cost_total, 2),
            "crypto_equity": round(crypto_equity, 2),
            "crypto_floating_pnl": round(crypto_floating_pnl, 2),
            "crypto_pnl": round(crypto_total_pnl, 2),
            "crypto_details": crypto_pos_details,
            "total_cash": round(total_cash, 2),
            "total_assets": round(total_assets, 2),
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "poly_open": len(self.poly_positions),
            "crypto_open": len(self.crypto_positions),
            "total_trades": len(self.trade_history)
        }
