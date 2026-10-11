import logging
import math
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class MeanReversionStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "3. Возврат к средней (RSI + BB)"
        self.position_size = 15.0
        self.stop_loss_pct = 0.018

    def calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def calculate_bollinger(self, closes: List[float], period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None
        sub = closes[-period:]
        sma = sum(sub) / period
        variance = sum((x - sma) ** 2 for x in sub) / period
        std_dev = math.sqrt(variance)
        upper = sma + num_std * std_dev
        lower = sma - num_std * std_dev
        return sma, upper, lower

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        closes = [c["close"] for c in candles[:-1]]  # confirmed bars
        rsi = self.calculate_rsi(closes, period=14)
        bb = self.calculate_bollinger(closes, period=20, num_std=2.0)
        if not bb:
            return
        sma, upper_bb, lower_bb = bb

        # 1. Check existing positions
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            side = pos["side"]
            entry = pos["entry"]
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            if side == "BUY":
                if sl and current_price <= sl:
                    to_close.append((pid, current_price, "Стоп-лосс (-1.8%)"))
                elif current_price >= tp and current_price >= entry * 1.015:  # Target hit and covers fees!
                    to_close.append((pid, current_price, f"Тейк-профит к средней (+{(current_price-entry)/entry*100:.1f}%)"))
            elif side == "SELL":
                if sl and current_price >= sl:
                    to_close.append((pid, current_price, "Стоп-лосс (-1.8%)"))
                elif current_price <= tp and current_price <= entry * 0.985:  # Target hit and covers fees!
                    to_close.append((pid, current_price, f"Тейк-профит к средней (+{(entry-current_price)/entry*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[RSI ОТСКОК — ЗАКРЫТИЕ ПОЗИЦИИ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code> ({res['side']})\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${res['entry']:.2f}</code> ➔ <b>Выход:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>PnL:</b> <code>{res['pnl']:+.2f}$</code> (<code>{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if we already have position in this symbol
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if self.portfolio.cash < self.position_size:
            return

        # 3. Entry conditions
        # Extreme Oversold: RSI < 30 and price <= lower BB -> Bounce BUY
        if rsi < 32 and current_price <= lower_bb * 1.002:
            sl = round(current_price * (1.0 - self.stop_loss_pct), 2)
            tp = round(max(sma, current_price * 1.015), 2)
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Oversold Bounce (RSI={rsi:.1f})"
            )
            if trade_id:
                msg = (
                    f"🧲 <b>[RSI ОТСКОК — ВХОД В ЛОНГ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ)\n"
                    f"📉 <b>Индикатор:</b> RSI = <code>{rsi:.1f}</code> (Перепроданность)\n"
                    f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code>\n"
                    f"🎯 <b>Цель (средняя):</b> <code>${tp:.2f}</code> | SL: <code>${sl:.2f}</code>\n"
                    f"💰 <b>Банк:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # Extreme Overbought: RSI > 68 and price >= upper BB -> Reversal SELL
        elif rsi > 68 and current_price >= upper_bb * 0.998:
            sl = round(current_price * (1.0 + self.stop_loss_pct), 2)
            tp = round(min(sma, current_price * 0.985), 2)
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="SELL",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Overbought Reversal (RSI={rsi:.1f})"
            )
            if trade_id:
                msg = (
                    f"🧲 <b>[RSI ОТСКОК — ВХОД В ШОРТ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ШОРТ)\n"
                    f"📈 <b>Индикатор:</b> RSI = <code>{rsi:.1f}</code> (Перекупленность)\n"
                    f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code>\n"
                    f"🎯 <b>Цель (средняя):</b> <code>${tp:.2f}</code> | SL: <code>${sl:.2f}</code>\n"
                    f"💰 <b>Банк:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
