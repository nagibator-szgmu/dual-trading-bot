import logging
import math
import time
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class POCPullbackStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "8. POC Pullback Sniper (Volume Profile)"
        self.position_size = 15.0
        self.stop_loss_pct = 0.018    # base -1.8% SL
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

    def compute_volume_profile(self, candles: List[Dict[str, float]], num_bins: int = 50):
        if len(candles) < 25:
            return None
        
        # Analyze up to last 96 bars (24 hours of 15m candles)
        sub = candles[-96:] if len(candles) >= 96 else candles
        min_p = min(c["low"] for c in sub)
        max_p = max(c["high"] for c in sub)
        
        if max_p <= min_p:
            return None
            
        bin_size = (max_p - min_p) / num_bins
        volume_by_bin = [0.0] * num_bins
        
        for c in sub:
            l = c["low"]
            h = c["high"]
            v = c.get("vol", 0.0)
            idx_start = max(0, min(int((l - min_p) / bin_size), num_bins - 1))
            idx_end = max(0, min(int((h - min_p) / bin_size), num_bins - 1))
            if idx_end < idx_start:
                idx_start, idx_end = idx_end, idx_start
            count = max(1, idx_end - idx_start + 1)
            v_per_bin = v / count
            for i in range(idx_start, idx_end + 1):
                volume_by_bin[i] += v_per_bin
                
        poc_idx = max(range(num_bins), key=lambda i: volume_by_bin[i])
        poc_price = min_p + (poc_idx + 0.5) * bin_size
        return poc_price

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

            # Trailing Breakeven: when profit reaches +0.9%, lock SL at entry + 0.3%
            if not pos.get("breakeven") and current_price >= entry * 1.009:
                new_sl = self.round_price(entry * 1.003)
                pos["stop_loss"] = new_sl
                pos["breakeven"] = True
                sl = new_sl
                p_fmt = f"${entry:.4f}" if entry < 10 else f"${entry:.2f}"
                cur_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                sl_fmt = f"${new_sl:.4f}" if new_sl < 10 else f"${new_sl:.2f}"
                be_msg = (
                    f"🛡 <b>[POC PULLBACK — ПЕРЕВОД В БЕЗУБЫТОК]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                    f"🎯 <b>Вход:</b> <code>{p_fmt}</code> ➔ <b>Тек:</b> <code>{cur_fmt}</code> (+0.9%)\n"
                    f"🔒 <b>Новый Стоп-Лосс:</b> <code>{sl_fmt}</code> (гарантия прибыли +0.3%)"
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
                reason = f"Безубыток/Трейлинг (+{(current_price-entry)/entry*100:.1f}%)" if pos.get("breakeven") and current_price >= entry else "Стоп-лосс POC (-1.8%)"
                to_close.append((pid, current_price, reason))
            elif tp and current_price >= tp and current_price >= entry * 1.020:
                to_close.append((pid, current_price, f"Тейк-профит POC импульса (+{(current_price-entry)/entry*100:.1f}%)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                self.cooldowns[symbol] = time.time() + 2700  # 45 min cooldown
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[POC PULLBACK — ВЫХОД]</b>\n"
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

        # 3. Calculate Volume Profile POC
        poc_price = self.compute_volume_profile(candles)
        if not poc_price or atr14 <= 0:
            return

        # Volatility regime filter: ensure ATR is active (>= 0.75% of price)
        if (atr14 / current_price) < 0.0075:
            return

        # Volume confirmation on current bar: volume > 1.3x SMA20
        vols = [c.get("vol", 0.0) for c in candles[:-1]]
        vol_sma20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1.0
        last_candle = candles[-1]
        curr_vol = last_candle.get("vol", 0.0)
        vol_ok = curr_vol >= 1.3 * vol_sma20 if vol_sma20 > 0 else True

        # Bull defense check: candle close >= open
        bull_candle = last_candle.get("close", 0) >= last_candle.get("open", 0)

        # Pullback zone: current price is testing the POC support level from above (+0.8% to -0.4%)
        is_at_poc = (poc_price * 0.996 <= current_price <= poc_price * 1.008)

        # Check that market broke cleanly above POC previously (at least 1.0% breakout to prevent chop near POC)
        recent_highs = [c["high"] for c in candles[-15:]]
        had_breakout = max(recent_highs) >= poc_price * 1.010

        if is_at_poc and had_breakout and vol_ok and bull_candle:
            sl = self.round_price(min(current_price * (1.0 - self.stop_loss_pct), poc_price - 1.1 * atr14))
            tp = self.round_price(max(current_price * 1.022, current_price + 2.0 * atr14))
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Volume Profile POC Pullback (POC=${poc_price:.2f})"
            )
            if trade_id:
                p_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                tp_fmt = f"${tp:.4f}" if tp < 10 else f"${tp:.2f}"
                sl_fmt = f"${sl:.4f}" if sl < 10 else f"${sl:.2f}"
                poc_fmt = f"${poc_price:.4f}" if poc_price < 10 else f"${poc_price:.2f}"
                msg = (
                    f"🎯 <b>[POC PULLBACK — ТЕСТ УРОВНЯ ОБЪЕМА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ на отскок от POC)\n"
                    f"📊 <b>Point of Control (24h):</b> <code>{poc_fmt}</code>\n"
                    f"💵 <b>Цена входа:</b> <code>{p_fmt}</code> | Объем: $15\n"
                    f"🎯 <b>Цель (+2.0 ATR):</b> <code>{tp_fmt}</code> | 🛡 <b>SL (под POC):</b> <code>{sl_fmt}</code>\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
