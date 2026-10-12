import logging
import math
import time
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
        self.cooldowns: dict = {}

    def round_price(self, px: float) -> float:
        return round(px, 4 if px < 10.0 else 2)

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

    def calculate_atr(self, candles: List[Dict[str, float]], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(len(candles) - period, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            c_prev = candles[i - 1]["close"]
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            trs.append(tr)
        return sum(trs) / period

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        closes = [c["close"] for c in candles[:-1]]  # confirmed bars
        rsi = self.calculate_rsi(closes, period=14)
        bb = self.calculate_bollinger(closes, period=20, num_std=2.0)
        if not bb:
            return
        sma, upper_bb, lower_bb = bb
        atr14 = self.calculate_atr(candles, period=14)

        # 1. Check existing positions
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            side = pos["side"]
            entry = pos["entry"]
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            # Trailing Breakeven: when profit reaches +0.9%, lock SL at entry + 0.5%
            if not pos.get("breakeven") and current_price >= entry * 1.009:
                new_sl = self.round_price(entry * 1.005)
                pos["stop_loss"] = new_sl
                pos["breakeven"] = True
                sl = new_sl
                p_fmt = f"${entry:.4f}" if entry < 10 else f"${entry:.2f}"
                cur_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                sl_fmt = f"${new_sl:.4f}" if new_sl < 10 else f"${new_sl:.2f}"
                be_msg = (
                    f"🛡 <b>[RSI ОТСКОК — ПЕРЕВОД В БЕЗУБЫТОК]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                    f"🎯 <b>Вход:</b> <code>{p_fmt}</code> ➔ <b>Тек:</b> <code>{cur_fmt}</code> (+0.9%)\n"
                    f"🔒 <b>Новый Стоп-Лосс:</b> <code>{sl_fmt}</code> (гарантия прибыли +0.5%)"
                )
                await notifier.send_message(be_msg, parse_mode="HTML")
            elif pos.get("breakeven") and atr14 > 0:
                # Stage 2: Tightened trailing 0.5 ATR on strong profit (>= +2.0%), otherwise 0.7 ATR
                mult = 0.5 if current_price >= entry * 1.020 else 0.7
                trail_sl = self.round_price(current_price - mult * atr14)
                if trail_sl > pos.get("stop_loss", 0):
                    pos["stop_loss"] = trail_sl
                    sl = trail_sl

            if side == "BUY":
                if sl and current_price <= sl:
                    reason = f"Безубыток/Трейлинг (+{(current_price-entry)/entry*100:.1f}%)" if pos.get("breakeven") and current_price >= entry else "Стоп-лосс (-1.8%)"
                    to_close.append((pid, current_price, reason))
                elif current_price >= tp and current_price >= entry * 1.020:  # Minimum 2.0% profit target
                    to_close.append((pid, current_price, f"Тейк-профит к средней (+{(current_price-entry)/entry*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.cooldowns[symbol] = time.time() + 3600  # 1 hour cooldown to prevent churn loop
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

        # 2. Check if we already have position in this symbol or cooldown active
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if time.time() < self.cooldowns.get(symbol, 0):
            return

        if self.portfolio.cash < self.position_size:
            return

        # Volume filter: bar volume > 1.1 * SMA20
        vols = [c.get("vol", 0.0) for c in candles[:-1]]
        vol_sma20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0.0
        curr_vol = candles[-1].get("vol", 0.0)
        vol_ok = curr_vol >= 1.1 * vol_sma20 if vol_sma20 > 0 else True

        # 3. Entry conditions: RSI <= 30, Close <= Lower_BB * 0.997 and Volume Confirmation
        if rsi <= 30.0 and current_price <= lower_bb * 0.997 and vol_ok:
            sl = self.round_price(current_price * (1.0 - self.stop_loss_pct))
            # Guaranteed TP >= +2.0%, or return to SMA + 0.3%
            tp = self.round_price(max(sma * 1.003, current_price * 1.020))
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Oversold Bounce (RSI={rsi:.1f}, BB-Break)"
            )
            if trade_id:
                p_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                tp_fmt = f"${tp:.4f}" if tp < 10 else f"${tp:.2f}"
                sl_fmt = f"${sl:.4f}" if sl < 10 else f"${sl:.2f}"
                msg = (
                    f"🧲 <b>[RSI ОТСКОК — ВХОД В ЛОНГ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ)\n"
                    f"📉 <b>Индикатор:</b> RSI = <code>{rsi:.1f}</code> (≤30.0, BB-Прокол)\n"
                    f"💵 <b>Цена входа:</b> <code>{p_fmt}</code>\n"
                    f"🎯 <b>Цель (средняя):</b> <code>{tp_fmt}</code> (≥+2.0%) | SL: <code>{sl_fmt}</code>\n"
                    f"💰 <b>Банк:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
