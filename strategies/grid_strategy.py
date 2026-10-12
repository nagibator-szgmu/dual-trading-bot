import logging
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class GridStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "2. Спотовая Сетка (Trend-Hedged Grid)"
        self.grid_step_pct = 0.025  # 2.5% widened step to preserve cash and prevent inventory trap
        self.level_size = 10.0     # $10 per level
        self.max_levels_per_symbol = 3
        self.last_grid_action_price: Dict[str, float] = {}

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        # 1. Check existing positions for grid TP (price rose by >= 2.5% above entry)
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            entry = pos["entry"]
            profit_target = entry * (1.0 + self.grid_step_pct)
            
            if current_price >= profit_target:
                to_close.append((pid, current_price, f"Сетка: исполнен уровень продажи (+{self.grid_step_pct*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.last_grid_action_price[symbol] = exit_px
                msg = (
                    f"🟢 <b>[СЕТКА GRID — ПРИБЫЛЬ С ЦИКЛА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code>\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Куплено по:</b> <code>${res['entry']:.2f}</code> ➔ <b>Продано:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>Чистая прибыль:</b> <code>+{res['pnl']:.2f}$</code> (<code>+{res['pnl_pct']:.2f}%</code>)\n"
                    f"💰 <b>Банк сетки:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if we should open a new buy level on a dip
        active_pos_for_sym = [p for p in self.portfolio.positions.values() if p["symbol"] == symbol]
        if len(active_pos_for_sym) >= self.max_levels_per_symbol:
            return

        if self.portfolio.cash < self.level_size:
            return

        # Trend-Hedge: if price is heavily below short-term EMA, freeze adding dip levels to prevent catching falling knives
        if candles and len(candles) >= 20 and active_pos_for_sym:
            closes = [c["close"] for c in candles]
            ema20 = sum(closes[-20:]) / 20
            if current_price < ema20 * 0.985:  # falling knife condition
                return

        last_px = self.last_grid_action_price.get(symbol)
        should_buy = False

        if not active_pos_for_sym:
            # Initialize initial base level
            should_buy = True
        else:
            # If price dropped at least 2.0% below lowest active entry
            min_entry = min(p["entry"] for p in active_pos_for_sym)
            if current_price <= min_entry * (1.0 - self.grid_step_pct):
                should_buy = True

        if should_buy:
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.level_size,
                stop_loss=None,  # Spot Grid doesn't use tight stop loss (holds actual coin)
                take_profit=round(current_price * (1.0 + self.grid_step_pct), 2),
                strategy_detail=f"Spot Grid Level {len(active_pos_for_sym)+1}"
            )
            if trade_id:
                self.last_grid_action_price[symbol] = current_price
                msg = (
                    f"🧺 <b>[СЕТКА GRID — ПОКУПКА УРОВНЯ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code>\n"
                    f"💵 <b>Цена покупки:</b> <code>${current_price:.2f}</code>\n"
                    f"🎯 <b>Цель продажи:</b> <code>${round(current_price * (1.0 + self.grid_step_pct), 2):.2f}</code> (+{self.grid_step_pct*100:.1f}%)\n"
                    f"💰 <b>Размер уровня:</b> <code>${self.level_size:.2f}</code> | Свободно: <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
