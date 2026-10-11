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

            # Time-Stop: 4 hours (14400s) for liquidation bounce
            now_ts = time.time()
            open_ts = pos.get("opened_at_ts")
            if not open_ts and pos.get("time"):
                try:
                    open_ts = time.mktime(time.strptime(pos["time"], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    open_ts = now_ts
            time_elapsed = now_ts - (open_ts or now_ts)

            if sl and current_price <= sl:
                to_close.append((pid, current_price, "Стоп-лосс сквиза (-2.0%)"))
            elif tp and current_price >= tp:
                to_close.append((pid, current_price, "Тейк-профит V-отскока (+2.8%)"))
            elif time_elapsed >= 4 * 3600:
                to_close.append((pid, current_price, "Таймаут удержания (Time-Stop 4ч)"))

        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
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

        # 2. Check active positions
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if self.portfolio.cash < self.position_size:
            return

        # 3. Detect flash dump liquidation spike
        is_dump, drop_pct = self.detect_liquidation_wick(candles)
        if is_dump:
            sl = round(current_price * (1.0 - self.stop_loss_pct), 2)
            tp = round(current_price * (1.0 + self.take_profit_pct), 2)
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
                msg = (
                    f"🩸 <b>[ОХОТНИК ЗА СКВИЗАМИ — ВЫКУП ПРОЛИВА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (ЛОНГ на отскок)\n"
                    f"📉 <b>Пролив:</b> Импульсный слив на <code>{drop_pct*100:.1f}%</code> с выкупом фитиля\n"
                    f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code> | Объем: $15\n"
                    f"🎯 <b>Цель V-отскока:</b> <code>${tp:.2f}</code> (+2.8%) | 🛡 <b>SL:</b> <code>${sl:.2f}</code> (-2.0%)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
