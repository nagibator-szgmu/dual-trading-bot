import logging
import math
import time
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class VolatilitySqueezeStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "4. Квантовая Пружина (Squeeze)"
        self.position_size = 15.0
        self.stop_loss_pct = 0.018    # -1.8% SL
        self.take_profit_pct = 0.055   # +5.5% TP (R:R 1:3)
        self.cooldowns: dict = {}

    def round_price(self, px: float) -> float:
        return round(px, 4 if px < 10.0 else 2)

    def calculate_indicators(self, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return None
        closes = [c["close"] for c in candles]
        
        # Bollinger Bands (20, 2.0 std)
        sub20 = closes[-20:]
        sma20 = sum(sub20) / 20
        var = sum((x - sma20)**2 for x in sub20) / 20
        std = math.sqrt(var)
        upper_bb = sma20 + 2.0 * std
        lower_bb = sma20 - 2.0 * std
        
        # ATR (14)
        trs = []
        for i in range(len(candles) - 14, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            c_prev = candles[i-1]["close"]
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            trs.append(tr)
        atr14 = sum(trs) / 14

        # Keltner Channels (20 EMA ± 1.5 * ATR)
        keltner_upper = sma20 + 1.5 * atr14
        keltner_lower = sma20 - 1.5 * atr14

        # Squeeze Condition: Bollinger Bands inside Keltner Channels
        is_squeezed = (lower_bb > keltner_lower) and (upper_bb < keltner_upper)
        
        # Volume Spike (Volume > 1.6 * SMA Volume 20)
        vols = [c.get("vol", 1.0) for c in candles[-20:]]
        avg_vol = sum(vols) / len(vols)
        curr_vol = candles[-1].get("vol", 1.0)
        vol_confirmed = curr_vol >= 1.60 * avg_vol

        return {
            "sma20": sma20,
            "upper_bb": upper_bb,
            "lower_bb": lower_bb,
            "keltner_upper": keltner_upper,
            "keltner_lower": keltner_lower,
            "atr14": atr14,
            "is_squeezed": is_squeezed,
            "vol_confirmed": vol_confirmed
        }

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 30:
            return

        inds = self.calculate_indicators(candles)
        if not inds:
            return
        atr14 = inds.get("atr14", 0.0)

        # 1. Check existing positions for SL / TP
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            side = pos["side"]
            entry = pos["entry"]
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            # Breakeven Trailing: when profit reaches +1.5%, lock SL at entry + 0.5%
            if not pos.get("breakeven") and current_price >= entry * 1.015:
                new_sl = self.round_price(entry * 1.005)
                pos["stop_loss"] = new_sl
                pos["breakeven"] = True
                sl = new_sl
                p_fmt = f"${entry:.4f}" if entry < 10 else f"${entry:.2f}"
                cur_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                sl_fmt = f"${new_sl:.4f}" if new_sl < 10 else f"${new_sl:.2f}"
                be_msg = (
                    f"🛡 <b>[КВАНТОВАЯ ПРУЖИНА — ПЕРЕВОД В БЕЗУБЫТОК]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                    f"🎯 <b>Вход:</b> <code>{p_fmt}</code> ➔ <b>Тек:</b> <code>{cur_fmt}</code> (+1.5%)\n"
                    f"🔒 <b>Новый Стоп-Лосс:</b> <code>{sl_fmt}</code> (гарантия прибыли +0.5%)"
                )
                await notifier.send_message(be_msg, parse_mode="HTML")
            elif pos.get("breakeven") and atr14 > 0:
                # Stage 2: Dynamic trailing 0.6 ATR when profit >= +2.2%
                if current_price >= entry * 1.022:
                    trail_sl = self.round_price(current_price - 0.6 * atr14)
                    if trail_sl > pos.get("stop_loss", 0):
                        pos["stop_loss"] = trail_sl
                        sl = trail_sl

            if side == "BUY":
                if sl and current_price <= sl:
                    reason = f"Безубыток/Трейлинг (+{(current_price-entry)/entry*100:.1f}%)" if pos.get("breakeven") and current_price >= entry else "Стоп-лосс (-1.8%)"
                    to_close.append((pid, current_price, reason))
                elif tp and current_price >= tp:
                    to_close.append((pid, current_price, "Тейк-профит импульса (+5.5%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.cooldowns[symbol] = time.time() + 3600  # 1 hour cooldown to prevent churn loop
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[КВАНТОВАЯ ПРУЖИНА — СДЕЛКА ЗАКРЫТА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code>\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${res['entry']:.2f}</code> ➔ <b>Выход:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>Чистый PnL:</b> <code>{res['pnl']:+.2f}$</code> (<code>{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if already in position or cooldown active
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if time.time() < self.cooldowns.get(symbol, 0):
            return

        if self.portfolio.cash < self.position_size:
            return

        # 3. Entry: Squeeze Fire Breakout
        ind = self.calculate_indicators(candles[:-1])
        if not ind:
            return

        closed_bars = candles[:-1]
        last_closed = closed_bars[-1]
        highest_prev = max(c["high"] for c in closed_bars[-20:])
        confirmed_close = last_closed["close"]
        bullish_candle = last_closed.get("close", 0) > last_closed.get("open", 0)

        # Validated Squeeze Breakout: volatility was squeezed AND confirmed by volume spike (>=1.6x) AND bullish candle
        if ind["is_squeezed"] and ind["vol_confirmed"] and bullish_candle and confirmed_close > highest_prev * 0.999 and current_price > ind["upper_bb"]:
            sl = self.round_price(current_price * (1.0 - self.stop_loss_pct))
            tp = self.round_price(current_price * (1.0 + self.take_profit_pct))
            
            inds = {
                "bb_position": round((current_price - ind["lower_bb"]) / (ind["upper_bb"] - ind["lower_bb"]), 2) if ind["upper_bb"] > ind["lower_bb"] else 1.0,
                "atr_14": round(ind["atr14"], 4),
                "atr_percent": round(ind["atr14"] / current_price * 100, 2),
                "volatility_regime": "squeeze" if ind["is_squeezed"] else "breakout"
            }
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Squeeze Breakout (ATR={ind['atr14']:.2f})",
                indicators=inds
            )
            if trade_id:
                p_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                tp_fmt = f"${tp:.4f}" if tp < 10 else f"${tp:.2f}"
                sl_fmt = f"${sl:.4f}" if sl < 10 else f"${sl:.2f}"
                msg = (
                    f"💥 <b>[КВАНТОВАЯ ПРУЖИНА — ВЫХОД ИЗ СЖАТИЯ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ)\n"
                    f"⚡ <b>Паттерн:</b> Разжатие полос Боллинджера / Пробой канала\n"
                    f"💵 <b>Вход:</b> <code>{p_fmt}</code>\n"
                    f"🎯 <b>Тейк-профит:</b> <code>{tp_fmt}</code> (+5.5%) | 🛡 <b>SL:</b> <code>{sl_fmt}</code> (-1.8%)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
