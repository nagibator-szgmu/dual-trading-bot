import csv
import json
import os
import time
from typing import Dict, List, Any, Optional
from core.hf_sync import sync_from_hf, sync_to_hf

PORTFOLIO_FILE = "portfolio_state.json"
SNAPSHOTS_FILE = "snapshots.csv"
SIGNALS_FILE = "signals.csv"

TRADE_FIELDNAMES = [
    "trade_id",
    "timestamp_open",
    "timestamp_close",
    "strategy_id",
    "strategy_name",
    "symbol",
    "action",
    "side",
    "entry_price",
    "exit_price",
    "quantity",
    "position_size_usd",
    "realized_pnl_usd",
    "realized_pnl_percent",
    "fees_usd",
    "slippage_percent",
    "initial_sl_price",
    "initial_tp_price",
    "initial_rr",
    "max_risk_usd",
    "breakeven_moved",
    "breakeven_price",
    "trailing_activated",
    "max_unrealized_pnl_percent",
    "min_unrealized_pnl_percent",
    "bars_held",
    "hold_duration_minutes",
    "entry_reason",
    "exit_reason",
    "exit_type",
    "atr_14",
    "atr_percent",
    "rsi_14",
    "volume_ratio",
    "bb_position",
    "donchian_high_20",
    "ema_10",
    "ema_25",
    "ema_50",
    "wick_ratio",
    "distance_to_poc_percent",
    "volatility_regime",
    "cooldown_active",
    "is_pyramid_add",
    "pyramid_level",
    "cash_after_trade",
    "inventory_usd"
]

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

    def update_position_metrics(self, symbol: str, current_price: float):
        now_ts = time.time()
        for pos in self.positions.values():
            if pos.get("symbol") != symbol:
                continue
            entry = pos.get("entry", current_price)
            side = pos.get("side", "BUY")
            cur_pct = (current_price - entry) / entry * 100 if side == "BUY" else (entry - current_price) / entry * 100
            pos["max_unrealized_pnl_percent"] = round(max(float(pos.get("max_unrealized_pnl_percent", 0.0)), cur_pct), 2)
            pos["min_unrealized_pnl_percent"] = round(min(float(pos.get("min_unrealized_pnl_percent", 0.0)), cur_pct), 2)
            pos["bars_held"] = max(1, int((now_ts - pos.get("opened_at_ts", now_ts)) / 900))

    def open_position(
        self,
        symbol: str,
        side: str,
        price: float,
        amount_usd: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        strategy_detail: str,
        indicators: Optional[Dict[str, Any]] = None,
        cooldown_active: bool = False,
        is_pyramid_add: bool = False,
        pyramid_level: int = 1
    ) -> Optional[str]:
        if self.cash < amount_usd:
            return None
        self.cash = round(self.cash - amount_usd, 2)
        now_ts = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_ts))
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        trade_id = f"{self.code}_{symbol}_{int(now_ts * 1000)}"

        qty = round(amount_usd / price, 6) if price > 0 else 0.0
        init_risk_pct = abs(price - stop_loss) / price if stop_loss else 0.02
        init_reward_pct = abs(take_profit - price) / price if take_profit else 0.04
        init_rr = round(init_reward_pct / init_risk_pct, 2) if init_risk_pct > 0 else 0.0
        max_risk = round(amount_usd * init_risk_pct, 2)

        inds = indicators or {}
        pos_record = {
            "id": trade_id,
            "trade_id": trade_id,
            "strategy_id": self.code,
            "strategy_name": self.name,
            "symbol": symbol,
            "action": f"OPEN_{side}",
            "side": side,
            "entry": price,
            "entry_price": price,
            "amount": amount_usd,
            "position_size_usd": amount_usd,
            "quantity": qty,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "initial_sl_price": stop_loss,
            "initial_tp_price": take_profit,
            "initial_rr": init_rr,
            "max_risk_usd": max_risk,
            "strategy": strategy_detail,
            "entry_reason": strategy_detail,
            "indicators": inds,
            "opened_at_ts": now_ts,
            "timestamp_open": now_iso,
            "time": now_str,
            "breakeven_moved": False,
            "breakeven_price": None,
            "trailing_activated": False,
            "max_unrealized_pnl_percent": 0.0,
            "min_unrealized_pnl_percent": 0.0,
            "bars_held": 1,
            "cooldown_active": cooldown_active,
            "is_pyramid_add": is_pyramid_add,
            "pyramid_level": pyramid_level
        }
        self.positions[trade_id] = pos_record
        return trade_id

    def add_to_position(self, trade_id: str, add_price: float, add_amount_usd: float) -> bool:
        if trade_id not in self.positions or self.cash < add_amount_usd:
            return False
        self.cash = round(self.cash - add_amount_usd, 2)
        pos = self.positions[trade_id]
        old_amt = pos["amount"]
        old_entry = pos["entry"]
        new_amt = old_amt + add_amount_usd
        new_entry = ((old_amt * old_entry) + (add_amount_usd * add_price)) / new_amt
        pos["amount"] = new_amt
        pos["position_size_usd"] = new_amt
        pos["entry"] = round(new_entry, 4)
        pos["entry_price"] = round(new_entry, 4)
        pos["is_pyramid_add"] = True
        pos["pyramid_level"] = pos.get("pyramid_level", 1) + 1
        return True

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        reason: str,
        exit_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if trade_id not in self.positions:
            return None
        pos = self.positions.pop(trade_id)
        now_ts = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_ts))
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        side = pos.get("side", "BUY")
        entry = pos.get("entry", exit_price)
        amt = pos.get("amount", 0.0)

        if side == "BUY":
            pct = (exit_price - entry) / entry
        else:
            pct = (entry - exit_price) / entry

        is_maker = self.code in ["grid", "adaptive_grid"]
        fee_rate = 0.0002 if is_maker else 0.0005
        slippage_pct = 0.0 if is_maker else 0.05
        total_fees = round(amt * (fee_rate * 2), 4)

        net_pct = pct - (2 * fee_rate) - (slippage_pct / 100.0)
        pnl = round(amt * net_pct, 4)
        return_amt = max(0.0, amt + pnl)
        self.cash = round(self.cash + return_amt, 2)

        if not exit_type:
            r_lower = reason.lower()
            if "тейк" in r_lower or "tp" in r_lower or "профит" in r_lower or "уровень продажи" in r_lower:
                exit_type = "take_profit"
            elif "безубыток" in r_lower:
                exit_type = "breakeven"
            elif "трейлинг" in r_lower or "trail" in r_lower or "chandelier" in r_lower:
                exit_type = "trailing_stop"
            elif "стоп" in r_lower or "sl" in r_lower:
                exit_type = "stop_loss"
            else:
                exit_type = "signal"

        hold_duration_min = round((now_ts - pos.get("opened_at_ts", now_ts)) / 60.0, 1)
        bars_held = max(1, int(hold_duration_min / 15.0))
        inventory_usd = round(sum(p["amount"] for p in self.positions.values() if p["symbol"] == pos["symbol"]), 2)

        inds = pos.get("indicators", {})

        record = {
            "trade_id": trade_id,
            "timestamp_open": pos.get("timestamp_open", pos.get("time", "")),
            "timestamp_close": now_iso,
            "strategy_id": self.code,
            "strategy_name": self.name,
            "symbol": pos.get("symbol", ""),
            "action": f"CLOSE_{side}",
            "side": side,
            "entry_price": round(entry, 4),
            "exit_price": round(exit_price, 4),
            "quantity": pos.get("quantity", round(amt / entry, 6) if entry > 0 else 0.0),
            "position_size_usd": round(amt, 2),
            "realized_pnl_usd": round(pnl, 2),
            "realized_pnl_percent": round(net_pct * 100, 2),
            "fees_usd": total_fees,
            "slippage_percent": slippage_pct,
            "initial_sl_price": pos.get("initial_sl_price"),
            "initial_tp_price": pos.get("initial_tp_price"),
            "initial_rr": pos.get("initial_rr", 0.0),
            "max_risk_usd": pos.get("max_risk_usd", 0.0),
            "breakeven_moved": pos.get("breakeven_moved", False) or pos.get("breakeven", False),
            "breakeven_price": pos.get("breakeven_price"),
            "trailing_activated": pos.get("trailing_activated", False) or ("трейлинг" in reason.lower()),
            "max_unrealized_pnl_percent": round(pos.get("max_unrealized_pnl_percent", 0.0), 2),
            "min_unrealized_pnl_percent": round(pos.get("min_unrealized_pnl_percent", 0.0), 2),
            "bars_held": bars_held,
            "hold_duration_minutes": hold_duration_min,
            "entry_reason": pos.get("entry_reason", pos.get("strategy", "")),
            "exit_reason": reason,
            "exit_type": exit_type,
            "atr_14": inds.get("atr_14"),
            "atr_percent": inds.get("atr_percent"),
            "rsi_14": inds.get("rsi_14"),
            "volume_ratio": inds.get("volume_ratio"),
            "bb_position": inds.get("bb_position"),
            "donchian_high_20": inds.get("donchian_high_20"),
            "ema_10": inds.get("ema_10"),
            "ema_25": inds.get("ema_25"),
            "ema_50": inds.get("ema_50"),
            "wick_ratio": inds.get("wick_ratio"),
            "distance_to_poc_percent": inds.get("distance_to_poc_percent"),
            "volatility_regime": inds.get("volatility_regime", "normal"),
            "cooldown_active": pos.get("cooldown_active", False),
            "is_pyramid_add": pos.get("is_pyramid_add", False),
            "pyramid_level": pos.get("pyramid_level", 1),
            "cash_after_trade": round(self.cash, 2),
            "inventory_usd": inventory_usd,
            "time": now_str,
            "pnl": round(pnl, 2),
            "pnl_pct": round(net_pct * 100, 2),
            "entry": entry,
            "exit": exit_price
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
    def __init__(self, initial_budget: float = 200.0, **kwargs):
        # 4 Core Active Strategies ($200.00 each = $800.00 total bankroll)
        self.strat_poc = StrategyPortfolio("1. POC Pullback Sniper (Volume Profile)", "poc_pullback", initial_budget)
        self.strat_dump_hunter = StrategyPortfolio("2. Охотник за сквизами (Dump Hunter)", "dump_hunter", initial_budget)
        self.strat_adaptive_grid = StrategyPortfolio("3. Адаптивная Сетка (ATR Grid)", "adaptive_grid", initial_budget)
        self.strat_squeeze = StrategyPortfolio("4. Квантовая Пружина (Volatility Squeeze)", "squeeze", initial_budget)

        self.strategies = [
            self.strat_poc,
            self.strat_dump_hunter,
            self.strat_adaptive_grid,
            self.strat_squeeze
        ]

        # Archived/Deactivated strategies retained for historical metrics & zero data loss
        self.archived_strategies = [
            StrategyPortfolio("5. Long-Only Пробой (EMA50) [Архив]", "trend", 100.0),
            StrategyPortfolio("6. Спотовая Сетка (Trend Grid) [Архив]", "grid", 100.0),
            StrategyPortfolio("7. Возврат к средней (RSI) [Архив]", "reversion", 100.0),
            StrategyPortfolio("8. HyperCascade Sniper [Архив]", "cascade", 100.0)
        ]

        # Convenience backward compatibility references
        self.strat_trend = self.archived_strategies[0]
        self.strat_grid = self.archived_strategies[1]
        self.strat_reversion = self.archived_strategies[2]
        self.strat_cascade = self.archived_strategies[3]

        self.total_initial = len(self.strategies) * initial_budget
        self.peak_equity: Dict[str, float] = {s.code: initial_budget for s in self.strategies}

        # Auto-restore state from HF Dataset if local is empty/reset
        try:
            sync_from_hf(filenames=[PORTFOLIO_FILE, SNAPSHOTS_FILE, SIGNALS_FILE])
        except Exception:
            pass

        self.load()

    def load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    all_strats = self.strategies + self.archived_strategies
                    for s in all_strats:
                        if s.code in data:
                            s.from_dict(data[s.code])
                    # Rebalance budget to $200 for active 4 strategies if loaded from older $100 preset
                    for s in self.strategies:
                        if s.initial < 200.0:
                            diff = 200.0 - s.initial
                            s.initial = 200.0
                            s.cash = round(s.cash + diff, 2)
            except Exception:
                pass

    def save(self):
        temp_file = f"{PORTFOLIO_FILE}.tmp"
        all_strats = self.strategies + self.archived_strategies
        data = {s.code: s.to_dict() for s in all_strats}
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, PORTFOLIO_FILE)
        except Exception:
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        try:
            sync_to_hf(PORTFOLIO_FILE)
        except Exception:
            pass

    def record_snapshot(self, live_prices: Optional[Dict[str, float]] = None):
        live_prices = live_prices or {}
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        snapshot_records = []
        for s in self.strategies:
            stats = s.get_stats(live_prices)
            eq = stats["equity"]
            peak = max(self.peak_equity.get(s.code, s.initial), eq)
            self.peak_equity[s.code] = peak
            dd_pct = ((peak - eq) / peak * 100) if peak > 0 else 0.0

            row = {
                "timestamp": now_iso,
                "strategy_id": s.code,
                "strategy_name": s.name,
                "symbol": "ALL",
                "cash_usd": stats["cash"],
                "open_positions_count": stats["open_count"],
                "total_inventory_usd": stats["invested"],
                "unrealized_pnl_usd": stats["floating_pnl"],
                "unrealized_pnl_percent": round(stats["floating_pnl"] / stats["invested"] * 100, 2) if stats["invested"] > 0 else 0.0,
                "equity_usd": eq,
                "drawdown_from_peak_percent": round(dd_pct, 2)
            }
            snapshot_records.append(row)

        file_exists = os.path.exists(SNAPSHOTS_FILE)
        try:
            with open(SNAPSHOTS_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp", "strategy_id", "strategy_name", "symbol",
                    "cash_usd", "open_positions_count", "total_inventory_usd",
                    "unrealized_pnl_usd", "unrealized_pnl_percent",
                    "equity_usd", "drawdown_from_peak_percent"
                ])
                if not file_exists:
                    writer.writeheader()
                for r in snapshot_records:
                    writer.writerow(r)
            sync_to_hf(SNAPSHOTS_FILE)
        except Exception:
            pass

    def log_signal(
        self,
        strategy_id: str,
        strategy_name: str,
        symbol: str,
        signal_type: str,
        passed_filters: bool,
        reject_reason: str = "",
        indicators: Optional[Dict[str, Any]] = None
    ):
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        row = {
            "timestamp": now_iso,
            "strategy_id": strategy_id,
            "strategy_name": strategy_name,
            "symbol": symbol,
            "signal_type": signal_type,
            "passed_filters": passed_filters,
            "reject_reason": reject_reason,
            "indicator_values_json": json.dumps(indicators or {}, ensure_ascii=False)
        }
        file_exists = os.path.exists(SIGNALS_FILE)
        try:
            with open(SIGNALS_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp", "strategy_id", "strategy_name", "symbol",
                    "signal_type", "passed_filters", "reject_reason", "indicator_values_json"
                ])
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
        except Exception:
            pass

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
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDNAMES)
            writer.writeheader()

            for strat in self.strategies:
                for t in strat.trades:
                    # Clean dictionary matching all fieldnames
                    row = {}
                    for field in TRADE_FIELDNAMES:
                        val = t.get(field, "")
                        if val is None:
                            val = ""
                        row[field] = val

                    # Fallbacks for legacy trade records
                    if not row["strategy_id"]:
                        row["strategy_id"] = strat.code
                    if not row["strategy_name"]:
                        row["strategy_name"] = strat.name
                    if not row["trade_id"]:
                        row["trade_id"] = t.get("id", f"{strat.code}_{t.get('symbol','')}_{row['timestamp_open']}")
                    if not row["timestamp_open"]:
                        row["timestamp_open"] = t.get("time", "")
                    if not row["entry_price"]:
                        row["entry_price"] = t.get("entry", t.get("price", ""))
                    if not row["exit_price"]:
                        row["exit_price"] = t.get("exit", "")
                    if not row["realized_pnl_usd"]:
                        row["realized_pnl_usd"] = t.get("pnl", 0.0)
                    if not row["realized_pnl_percent"]:
                        row["realized_pnl_percent"] = t.get("pnl_pct", 0.0)
                    if not row["exit_reason"]:
                        row["exit_reason"] = t.get("reason", "")
                    if not row["exit_type"]:
                        r_low = str(row["exit_reason"]).lower()
                        if "тейк" in r_low or "профит" in r_low:
                            row["exit_type"] = "take_profit"
                        elif "безубыток" in r_low:
                            row["exit_type"] = "breakeven"
                        elif "трейлинг" in r_low:
                            row["exit_type"] = "trailing_stop"
                        elif "стоп" in r_low:
                            row["exit_type"] = "stop_loss"
                        else:
                            row["exit_type"] = "signal" if row["exit_reason"] else ""

                    writer.writerow(row)

                # Also export current active open positions as live OPEN records
                for pos_id, pos in strat.positions.items():
                    row = {field: pos.get(field, "") for field in TRADE_FIELDNAMES}
                    row["strategy_id"] = strat.code
                    row["strategy_name"] = strat.name
                    row["trade_id"] = pos_id
                    row["action"] = f"OPEN_{pos.get('side', 'BUY')}"
                    row["timestamp_open"] = pos.get("timestamp_open", pos.get("time", ""))
                    row["timestamp_close"] = "OPEN"
                    row["entry_price"] = pos.get("entry", "")
                    row["exit_price"] = ""
                    row["position_size_usd"] = pos.get("amount", "")
                    row["realized_pnl_usd"] = 0.0
                    row["realized_pnl_percent"] = 0.0
                    row["max_unrealized_pnl_percent"] = round(float(pos.get("max_unrealized_pnl_percent", 0.0)), 2)
                    row["min_unrealized_pnl_percent"] = round(float(pos.get("min_unrealized_pnl_percent", 0.0)), 2)
                    row["entry_reason"] = pos.get("entry_reason", pos.get("strategy", ""))
                    row["exit_reason"] = "POSITION_OPEN"
                    row["exit_type"] = "OPEN"
                    writer.writerow(row)

        return file_path

    def export_snapshots_csv(self, file_path: str = "snapshots.csv") -> str:
        if not os.path.exists(file_path):
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "strategy_id", "strategy_name", "symbol",
                    "cash_usd", "open_positions_count", "total_inventory_usd",
                    "unrealized_pnl_usd", "unrealized_pnl_percent",
                    "equity_usd", "drawdown_from_peak_percent"
                ])
        return file_path

    def export_signals_csv(self, file_path: str = "signals.csv") -> str:
        if not os.path.exists(file_path):
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "strategy_id", "strategy_name", "symbol",
                    "signal_type", "passed_filters", "reject_reason", "indicator_values_json"
                ])
        return file_path
