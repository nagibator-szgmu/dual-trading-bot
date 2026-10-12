import logging
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class AdaptiveGridStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "5. Адаптивная Сетка (ATR)"
        self.level_size = 10.0
        self.max_levels_per_symbol = 3
        self.last_grid_action_price: Dict[str, float] = {}

    def calculate_atr(self, candles: List[Dict[str, float]], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(len(candles) - period, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            c_prev = candles[i-1]["close"]
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            trs.append(tr)
        return sum(trs) / period

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        atr14 = self.calculate_atr(candles, period=14)
        atr_pct = (atr14 / current_price) if current_price > 0 else 0.02
        # Dynamic step: calibrated to max(2.0%, 2.1 * ATR/Price) to survive trending sell-offs
        dynamic_step = max(0.020, min(0.048, 2.1 * atr_pct))

        # 1. Check existing positions for dynamic TP
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            entry = pos["entry"]
            target_step = pos.get("step_pct", dynamic_step)
            profit_target = entry * (1.0 + target_step)
            
            if current_price >= profit_target:
                to_close.append((pid, current_price, f"Адаптивная сетка: закрыт шаг (+{target_step*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.last_grid_action_price[symbol] = exit_px
                msg = (
                    f"🟢 <b>[АДАПТИВНАЯ СЕТКА — ПРИБЫЛЬ С ЦИКЛА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code>\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Куплено по:</b> <code>${res['entry']:.2f}</code> ➔ <b>Продано:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>Чистая прибыль:</b> <code>+{res['pnl']:.2f}$</code> (<code>+{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Банк сетки:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if we should open a new buy level on a dip
        active_pos_for_sym = [p for p in self.portfolio.positions.values() if p["symbol"] == symbol]
        if len(active_pos_for_sym) >= self.max_levels_per_symbol:
            return

        if self.portfolio.cash < self.level_size:
            return

        # Trend safety: if price is below EMA25, don't open secondary levels to prevent catching deep waterfall crashes
        if len(candles) >= 25 and active_pos_for_sym:
            closes = [c["close"] for c in candles]
            ema25 = sum(closes[-25:]) / 25
            if current_price < ema25 * 0.985:
                return

        should_buy = False
        if not active_pos_for_sym:
            # Seed initial position
            should_buy = True
        else:
            # If price dropped at least dynamic_step below lowest active level
            min_entry = min(p["entry"] for p in active_pos_for_sym)
            if current_price <= min_entry * (1.0 - dynamic_step):
                should_buy = True

        if should_buy:
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.level_size,
                stop_loss=None,
                take_profit=round(current_price * (1.0 + dynamic_step), 2),
                strategy_detail=f"Adaptive Grid Level {len(active_pos_for_sym)+1} (Step={dynamic_step*100:.1f}%)"
            )
            if trade_id:
                self.portfolio.positions[trade_id]["step_pct"] = dynamic_step
                self.last_grid_action_price[symbol] = current_price
                msg = (
                    f"🕸️ <b>[АДАПТИВНАЯ СЕТКА — НОВЫЙ УРОВЕНЬ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code>\n"
                    f"📐 <b>Динамический шаг (ATR):</b> <code>{dynamic_step*100:.2f}%</code>\n"
                    f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code> | Объем: $10\n"
                    f"🎯 <b>Цель продажи:</b> <code>${current_price * (1.0 + dynamic_step):.2f}</code>\n"
                    f"💰 <b>Свободный кэш:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
