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
        self.name = "6. Охотник за сквизами (Dump)"
        self.position_size = 15.0
        self.stop_loss_pct = 0.020    # -2.0% SL
        self.take_profit_pct = 0.028   # +2.8% TP
        self.cooldowns: dict = {}

    def round_price(self, px: float) -> float:
        return round(px, 4 if px < 10.0 else 2)

    def detect_liquidation_wick(self, candles: List[Dict[str, float]]):
        if len(candles) < 20:
            return False, 0.0
        
        last_candle = candles[-1]
        prev_candles = candles[-20:-1]
        
        closes = [c["close"] for c in prev_candles]
        sma = sum(closes) / len(closes)
        var = sum((x - sma)**2 for x in closes) / len(closes)
        std = math.sqrt(var)
        lower_band = sma - 2.2 * std

        # Drop magnitude on current/previous bar
        o = last_candle["open"]
        l = last_candle["low"]
        c = last_candle["close"]
        drop_pct = (o - l) / o if o > 0 else 0.0
        
        # Wick size (buyers started absorbing the dump)
        wick_size = (c - l) / (o - l) if (o - l) > 0 else 0.0
        
        # Trigger: drop > 2.2% or pierced lower 2.2 std band and formed a lower shadow/wick >= 30%
        is_dump = (drop_pct >= 0.020 or l <= lower_band) and (wick_size >= 0.25)
        return is_dump, drop_pct

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        # 1. Check existing positions
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue
            entry = pos["entry"]
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            # Trailing Breakeven: when profit reaches +0.8%, lock SL at entry + 0.2%
            if not pos.get("breakeven") and current_price >= entry * 1.008:
                new_sl = self.round_price(entry * 1.002)
                pos["stop_loss"] = new_sl
                pos["breakeven"] = True
                sl = new_sl
                p_fmt = f"${entry:.4f}" if entry < 10 else f"${entry:.2f}"
                cur_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                sl_fmt = f"${new_sl:.4f}" if new_sl < 10 else f"${new_sl:.2f}"
                be_msg = (
                    f"🛡 <b>[ОХОТНИК ЗА СКВИЗАМИ — ПЕРЕВОД В БЕЗУБЫТОК]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                    f"🎯 <b>Вход:</b> <code>{p_fmt}</code> ➔ <b>Тек:</b> <code>{cur_fmt}</code> (+0.8%)\n"
                    f"🔒 <b>Новый Стоп-Лосс:</b> <code>{sl_fmt}</code> (гарантия прибыли +0.2%)"
                )
                await notifier.send_message(be_msg, parse_mode="HTML")

            if sl and current_price <= sl:
                reason = "Безубыток (+0.2%)" if pos.get("breakeven") and current_price >= entry else "Стоп-лосс сквиза (-2.0%)"
                to_close.append((pid, current_price, reason))
            elif tp and current_price >= tp:
                to_close.append((pid, current_price, "Тейк-профит V-отскока (+2.8%)"))

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
        is_dump, drop_pct = self.detect_liquidation_wick(candles)
        if is_dump:
            sl = self.round_price(current_price * (1.0 - self.stop_loss_pct))
            tp = self.round_price(current_price * (1.0 + self.take_profit_pct))
            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.position_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"Flash Dump Wick Rebound (-{drop_pct*100:.1f}%)"
            )
            if trade_id:
                p_fmt = f"${current_price:.4f}" if current_price < 10 else f"${current_price:.2f}"
                tp_fmt = f"${tp:.4f}" if tp < 10 else f"${tp:.2f}"
                sl_fmt = f"${sl:.4f}" if sl < 10 else f"${sl:.2f}"
                msg = (
                    f"🩸 <b>[ОХОТНИК ЗА СКВИЗАМИ — ВЫКУП ПРОЛИВА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ на отскок)\n"
                    f"📉 <b>Пролив:</b> Импульсный слив на <code>{drop_pct*100:.1f}%</code> с выкупом фитиля\n"
                    f"💵 <b>Цена входа:</b> <code>{p_fmt}</code> | Объем: $15\n"
                    f"🎯 <b>Цель V-отскока:</b> <code>{tp_fmt}</code> (+2.8%) | 🛡 <b>SL:</b> <code>{sl_fmt}</code> (-2.0%)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
