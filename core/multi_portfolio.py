import json
import os
import time
from typing import Dict, List, Any, Optional

PORTFOLIO_FILE = "portfolio_state.json"

class StrategyPortfolio:
    def __init__(self, name: str, code: str, initial_balance: float = 100.0):
        self.name = name
        self.code = code
        self.cash = initial_balance
        self.initial = initial_balance
        self.positions: Dict[str, Any] = {}
        self.trades: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "cash": self.cash,
            "initial": self.initial,
            "positions": self.positions,
            "trades": self.trades[-5000:]
        }

    def from_dict(self, data: Dict[str, Any]):
        self.cash = data.get("cash", self.cash)
        self.initial = data.get("initial", self.initial)
        self.positions = data.get("positions", {})
        self.trades = data.get("trades", [])

    def open_position(self, symbol: str, side: str, price: float, amount_usd: float, stop_loss: Optional[float], take_profit: Optional[float], strategy_detail: str):
        if self.cash < amount_usd:
            return None
        self.cash -= amount_usd
        trade_id = f"{self.code}_{symbol}_{int(time.time()*1000)}"
        self.positions[trade_id] = {
            "id": trade_id,
            "symbol": symbol,
            "side": side,
            "entry": price,
            "amount": amount_usd,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy_detail,
            "opened_at_ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trades.append({
            "action": f"OPEN_{side}",
            "symbol": symbol,
            "price": price,
            "amount": amount_usd,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        return trade_id

    def close_position(self, trade_id: str, exit_price: float, reason: str):
        if trade_id not in self.positions:
            return None
        pos = self.positions.pop(trade_id)
        side = pos["side"]
        entry = pos["entry"]
        amt = pos["amount"]

        if side == "BUY":
            pct = (exit_price - entry) / entry
        else:
            pct = (entry - exit_price) / entry

        # Realistic fee model: Grid uses limit Maker (0.02%), Trend/Reversion use market Taker (0.05% + 0.05% slippage)
        fee_rate = 0.0002 if self.code == "grid" else 0.0010
        net_pct = pct - (2 * fee_rate)
        pnl = amt * net_pct
        return_amt = max(0.0, amt + pnl)
        self.cash += return_amt

        record = {
            "action": f"CLOSE_{side}",
            "trade_id": trade_id,
            "symbol": pos["symbol"],
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round(net_pct * 100, 2),
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trades.append(record)
        return record

    def get_stats(self, live_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        live_prices = live_prices or {}
        cost_in_pos = sum(p["amount"] for p in self.positions.values())
        cur_pos_val = 0.0
        pos_details = []

        for pid, pos in self.positions.items():
            sym = pos["symbol"]
            side = pos["side"]
            amt = pos["amount"]
            entry = pos["entry"]
            cur = live_prices.get(sym, entry)

            if side == "BUY":
                ratio = (cur - entry) / entry
            else:
                ratio = (entry - cur) / entry

            pos_pnl = amt * ratio
            cur_pos_val += max(0.0, amt + pos_pnl)

            pos_details.append({
                "id": pid,
                "symbol": sym,
                "side": side,
                "entry": entry,
                "current": cur,
                "pnl": round(pos_pnl, 2),
                "pnl_pct": round(ratio * 100, 2),
                "sl": pos.get("stop_loss"),
                "tp": pos.get("take_profit")
            })

        equity = self.cash + cur_pos_val
        floating_pnl = cur_pos_val - cost_in_pos
        total_pnl = equity - self.initial
        total_pnl_pct = (total_pnl / self.initial) * 100 if self.initial > 0 else 0

        closed_trades = [t for t in self.trades if "CLOSE" in t.get("action", "")]
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

        return {
            "name": self.name,
            "code": self.code,
            "cash": round(self.cash, 2),
            "invested": round(cost_in_pos, 2),
            "equity": round(equity, 2),
            "floating_pnl": round(floating_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "open_count": len(self.positions),
            "closed_count": len(closed_trades),
            "win_rate": round(win_rate, 1),
            "positions": pos_details
        }


class TournamentManager:
    def __init__(self, initial_budget: float = 100.0):
        self.strat_trend = StrategyPortfolio("1. Long-Only Пробой (EMA50)", "trend", initial_budget)
        self.strat_grid = StrategyPortfolio("2. Спотовая Сетка (Trend Grid)", "grid", initial_budget)
        self.strat_reversion = StrategyPortfolio("3. Возврат к средней (RSI)", "reversion", initial_budget)
        self.strat_squeeze = StrategyPortfolio("4. Квантовая Пружина (Squeeze)", "squeeze", initial_budget)
        self.strat_adaptive_grid = StrategyPortfolio("5. Адаптивная Сетка (ATR)", "adaptive_grid", initial_budget)
        self.strat_dump_hunter = StrategyPortfolio("6. Охотник за сквизами (Dump)", "dump_hunter", initial_budget)
        self.strategies = [
            self.strat_trend,
            self.strat_grid,
            self.strat_reversion,
            self.strat_squeeze,
            self.strat_adaptive_grid,
            self.strat_dump_hunter
        ]
        self.total_initial = len(self.strategies) * initial_budget
        self.load()

    def load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for s in self.strategies:
                        if s.code in data:
                            s.from_dict(data[s.code])
            except Exception:
                pass

    def save(self):
        temp_file = f"{PORTFOLIO_FILE}.tmp"
        data = {s.code: s.to_dict() for s in self.strategies}
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, PORTFOLIO_FILE)
        except Exception:
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def get_leaderboard(self, live_prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
        strats = [s.get_stats(live_prices) for s in self.strategies]
        strats.sort(key=lambda s: s["total_pnl"], reverse=True)
        return strats

    def get_summary(self, live_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        strats = self.get_leaderboard(live_prices)
        total_equity = sum(s["equity"] for s in strats)
        total_cash = sum(s["cash"] for s in strats)
        total_open = sum(s["open_count"] for s in strats)
        total_pnl = total_equity - self.total_initial
        total_pnl_pct = (total_pnl / self.total_initial) * 100 if self.total_initial > 0 else 0.0
        return {
            "total_equity": round(total_equity, 2),
            "total_cash": round(total_cash, 2),
            "total_open_positions": total_open,
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "leaderboard": strats
        }

    def export_csv(self, file_path: str = "tournament_trades.csv") -> str:
        import csv
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp",
                "Strategy",
                "Symbol",
                "Action",
                "Side",
                "Entry_Price",
                "Exit_Price",
                "PnL_USD",
                "PnL_Percent",
                "Reason"
            ])
            for strat in self.strategies:
                for t in strat.trades:
                    writer.writerow([
                        t.get("time", ""),
                        strat.name,
                        t.get("symbol", ""),
                        t.get("action", ""),
                        t.get("side", ""),
                        t.get("entry", t.get("price", "")),
                        t.get("exit", ""),
                        t.get("pnl", 0.0),
                        f"{t.get('pnl_pct', 0.0)}%",
                        t.get("reason", "")
                    ])
        return file_path
