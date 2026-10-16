import logging
import math
import time
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class FlashDumpHunterStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "2. Охотник за сквизами (Dump Hunter)"
        self.position_size = 45.0
        self.stop_loss_pct = 0.020    # -2.0% SL
        self.take_profit_pct = 0.028   # +2.8% TP
        self.cooldowns: dict = {}

    def round_price(self, px: float) -> float:
        return round(px, 4 if px < 10.0 else 2)

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

    def detect_liquidation_wick(self, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return False, 0.0, 0.022
        
        last_candle = candles[-1]
        prev_candles = candles[-20:-1]
        
        # 1. Volume SMA20
        vols = [c.get("vol", 0.0) for c in prev_candles]
        vol_sma = sum(vols) / len(vols) if vols else 1.0
        curr_vol = last_candle.get("vol", 0.0)
        vol_ratio = curr_vol / vol_sma if vol_sma > 0 else 1.0
        
        # 2. Drop depth: dip from high of last 4 candles
        recent_highs = [c["high"] for c in candles[-5:]]
        max_high = max(recent_highs)
        o = last_candle["open"]
        l = last_candle["low"]
        h = last_candle["high"]
        c = last_candle["close"]
        
        drop_from_high = (max_high - l) / max_high if max_high > 0 else 0.0
        candle_range = (h - l) if (h - l) > 0 else 0.0001
        
        # Lower wick size: buyers absorbed the sell-off off the lows
        lower_wick = min(o, c) - l
        wick_ratio = lower_wick / candle_range
        
        # Close in upper portion: buyers regained control into bar close
        close_location = (c - l) / candle_range
        
        # Rigorous filters from quantitative audit:
        # - drop >= 2.2% from recent 4-bar high
        # - wick_ratio >= 0.50 (at least 50% lower shadow)
        # - volume >= 2.5x SMA20
        # - close in upper portion (close_location >= 0.50)
        is_dump = (drop_from_high >= 0.022) and (wick_ratio >= 0.50) and (vol_ratio >= 2.5) and (close_location >= 0.50)
        
        # Target TP: +2.2% on standard rebound, +3.2% if explosive volume
        target_tp = 0.032 if vol_ratio >= 3.5 else 0.022
        
        return is_dump, drop_from_high, target_tp, wick_ratio, vol_ratio

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        atr14 = self.calculate_atr(candles, period=14)

        # 1. Check existing positions
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
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
                    f"🛡 <b>[ОХОТНИК ЗА СКВИЗАМИ — ПЕРЕВОД В БЕЗУБЫТОК]</b>\n"
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

            if sl and current_price <= sl:
                reason = f"Безубыток/Трейлинг (+{(current_price-entry)/entry*100:.1f}%)" if pos.get("breakeven") and current_price >= entry else "Стоп-лосс сквиза (-2.0%)"
                to_close.append((pid, current_price, reason))
            elif tp and current_price >= tp and current_price >= entry * 1.020:
                to_close.append((pid, current_price, f"Тейк-профит V-отскока (+{(current_price-entry)/entry*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.cooldowns[symbol] = time.time() + 3600  # 1 hour cooldown to prevent churn loop
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[ОХОТНИК ЗА СКВИЗАМИ — ВЫХОД]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code>\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${res['entry']:.2f}</code> ➔ <b>Выход:</b> <code>${res['exit']:.2f}</code>\n"
                    f"💵 <b>Чистый PnL:</b> <code>{res['pnl']:+.2f}$</code> (<code>{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check active positions & cooldown
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if time.time() < self.cooldowns.get(symbol, 0):
            return

        if self.portfolio.cash < self.position_size:
            return

        # 3. Detect flash dump liquidation spike
        is_dump, drop_pct, tp_target_pct, wick_r, vol_r = self.detect_liquidation_wick(candles)
        if is_dump:
            sl = self.round_price(current_price * (1.0 - self.stop_loss_pct))
            tp = self.round_price(current_price * (1.0 + tp_target_pct))
            inds = {
                "wick_ratio": round(wick_r, 2),
                "drop_pct": round(drop_pct * 100, 2),
                "volume_ratio": round(vol_r, 2),
                "atr_14": round(atr14, 4),
                "volatility_regime": "high"
            }
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Flash Dump Wick Rebound (-{drop_pct*100:.1f}%)",
                indicators=inds
            )
            if trade_id:
                p_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                tp_fmt = f"${tp:.4f}" if tp < 10 else f"${tp:.2f}"
                sl_fmt = f"${sl:.4f}" if sl < 10 else f"${sl:.2f}"
                msg = (
                    f"🩸 <b>[ОХОТНИК ЗА СКВИЗАМИ — ВЫКУП ПРОЛИВА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ на отскок)\n"
                    f"📉 <b>Пролив:</b> Импульсный слив на <code>{drop_pct*100:.1f}%</code> с выкупом фитиля ≥50%\n"
                    f"💵 <b>Цена входа:</b> <code>{p_fmt}</code> | Объем: ${self.position_size:.0f}\n"
                    f"🎯 <b>Цель V-отскока:</b> <code>{tp_fmt}</code> (+{tp_target_pct*100:.1f}%) | 🛡 <b>SL:</b> <code>{sl_fmt}</code> (-2.0%)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
