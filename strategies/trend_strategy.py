import logging
import html
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class TrendStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "1. Трендовый пробой (15m)"
        self.position_size = 15.0
        self.stop_loss_pct = 0.015
        self.take_profit_pct = 0.035

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        # 1. Check existing positions for SL / TP
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            side = pos["side"]
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")
            if side == "BUY":
                if sl and current_price <= sl:
                    to_close.append((pid, current_price, "Стоп-лосс (-1.5%)"))
                elif tp and current_price >= tp:
                    to_close.append((pid, current_price, "Тейк-профит (+3.5%)"))
            elif side == "SELL":
                if sl and current_price >= sl:
                    to_close.append((pid, current_price, "Стоп-лосс (-1.5%)"))
                elif tp and current_price <= tp:
                    to_close.append((pid, current_price, "Тейк-профит (+3.5%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[ТРЕНД 15m — СДЕЛКА ЗАКРЫТА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code> ({res['side']})\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${res['entry']:.2f}</code> ➔ <b>Выход:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>PnL:</b> <code>{res['pnl']:+.2f}$</code> (<code>{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Баланс тренд-банка:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if we already have position in this symbol
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if self.portfolio.cash < self.position_size:
            return

        # 3. Indicators on confirmed closed bar
        closed_bars = candles[:-1]
        closes = [c["close"] for c in closed_bars]
        ema_fast = sum(closes[-10:]) / 10
        ema_slow = sum(closes[-25:]) / 25
        highest_prev = max(c["high"] for c in closed_bars[-21:-1])
        lowest_prev = min(c["low"] for c in closed_bars[-21:-1])
        confirmed_close = closes[-1]

        signal = None
        sl = None
        tp = None

        if confirmed_close > highest_prev and ema_fast > ema_slow:
            signal = "BUY"
            sl = round(current_price * (1.0 - self.stop_loss_pct), 2)
            tp = round(current_price * (1.0 + self.take_profit_pct), 2)
        elif confirmed_close < lowest_prev and ema_fast < ema_slow:
            signal = "SELL"
            sl = round(current_price * (1.0 + self.stop_loss_pct), 2)
            tp = round(current_price * (1.0 - self.take_profit_pct), 2)

        if signal:
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side=signal,
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail="Donchian Breakout + EMA"
            )
            if trade_id:
                side_emoji = "🚀 ЛОНГ" if signal == "BUY" else "🔻 ШОРТ"
                alert = (
                    f"⚡ <b>[ТРЕНД 15m — ВХОД В ПОЗИЦИЮ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> ({side_emoji})\n"
                    f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code>\n"
                    f"🛡 <b>Стоп-лосс:</b> <code>${sl:.2f}</code> (-1.5%)\n"
                    f"🎯 <b>Тейк-профит:</b> <code>${tp:.2f}</code> (+3.5%)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(alert, parse_mode="HTML")
