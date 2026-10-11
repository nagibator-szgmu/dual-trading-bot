import logging
import math
import time
from typing import List, Dict, Any, Optional
from core.multi_portfolio import StrategyPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class HyperCascadeSniperStrategy:
    def __init__(self, portfolio: StrategyPortfolio):
        self.portfolio = portfolio
        self.name = "7. HyperCascade Sniper (Пирамидинг)"
        self.stage1_size = 25.0
        self.stage2_size = 20.0
        self.stage3_size = 15.0
        self.initial_sl_pct = 0.015  # -1.5% SL on stage 1

    def detect_breakout_and_volume(self, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return False, 0.0, 0.0

        closed_candles = candles[:-1]
        vols = [c.get("vol", 1.0) for c in closed_candles[-20:]]
        avg_vol = sum(vols) / len(vols) if vols else 1.0
        last_vol = closed_candles[-1].get("vol", 1.0)
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

        highest_close = max(c["close"] for c in closed_candles[-20:-1])
        confirmed_breakout = closed_candles[-1]["close"] > highest_close

        # Trigger: volume spike >= 3.0x average and breakout of 20-period range
        is_signal = confirmed_breakout and (vol_ratio >= 2.8)
        return is_signal, vol_ratio, highest_close

    async def update(self, symbol: str, current_price: float, candles: List[Dict[str, float]]):
        if len(candles) < 25:
            return

        # 1. Manage existing positions: Stop Loss, Pyramiding, Trailing Exit
        to_close = []
        for pid, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue

            entry = pos["entry"]
            sl = pos.get("stop_loss")
            stage = pos.get("stage", 1)
            highest_px = pos.get("highest_price", entry)

            if current_price > highest_px:
                pos["highest_price"] = current_price
                highest_px = current_price

            now_ts = time.time()
            open_ts = pos.get("opened_at_ts")
            if not open_ts and pos.get("time"):
                try:
                    open_ts = time.mktime(time.strptime(pos["time"], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    open_ts = now_ts
            time_elapsed = now_ts - (open_ts or now_ts)

            # Pyramiding Level 2: +1.8% gain -> Add $20 & Shift SL to Breakeven (+0.2%)
            if stage == 1 and current_price >= entry * 1.018:
                if self.portfolio.add_to_position(pid, current_price, self.stage2_size):
                    pos["stage"] = 2
                    stage = 2
                    new_sl = round(pos["entry"] * 1.002, 4)
                    pos["stop_loss"] = new_sl
                    sl = new_sl
                    msg = (
                        f"🚀 <b>[HYPERCASCADE — ДОБОР 2-ГО УРОВНЯ & БЕЗУБЫТОК]</b>\n"
                        f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                        f"📈 <b>Импульс подтвержден:</b> +1.8% хода цены!\n"
                        f"💰 <b>Добавлено в позицию:</b> +${self.stage2_size:.2f} (Всего: ${pos['amount']:.2f})\n"
                        f"🎯 <b>Новая средняя цена:</b> <code>${pos['entry']:.4f}</code>\n"
                        f"🛡 <b>Стоп-лосс перенесен в безубыток:</b> <code>${new_sl:.4f}</code> (РИСК = $0.00)"
                    )
                    await notifier.send_message(msg, parse_mode="HTML")

            # Pyramiding Level 3: +3.5% gain from average -> Add $15 & Shift SL to Guaranteed Profit (+1.8%)
            elif stage == 2 and current_price >= pos["entry"] * 1.035:
                if self.portfolio.add_to_position(pid, current_price, self.stage3_size):
                    pos["stage"] = 3
                    stage = 3
                    new_sl = round(pos["entry"] * 1.018, 4)
                    pos["stop_loss"] = new_sl
                    sl = new_sl
                    msg = (
                        f"🔥 <b>[HYPERCASCADE — ФИНАЛЬНЫЙ РАЗГОН 3-ГО УРОВНЯ]</b>\n"
                        f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                        f"⚡ <b>Ракета ускоряется:</b> +3.5% от средней цены!\n"
                        f"💰 <b>Добавлено в позицию:</b> +${self.stage3_size:.2f} (Суммарная поза: ${pos['amount']:.2f})\n"
                        f"🎯 <b>Новая средняя:</b> <code>${pos['entry']:.4f}</code>\n"
                        f"🔒 <b>Гарантированная прибыль зафиксирована:</b> <code>${new_sl:.4f}</code> (+1.8%)"
                    )
                    await notifier.send_message(msg, parse_mode="HTML")

            # Trailing Chandelier Exit for active stages
            if stage >= 2:
                pullback_threshold = highest_px * 0.982  # -1.8% from peak
                if current_price <= pullback_threshold:
                    pnl_pct = (current_price - pos["entry"]) / pos["entry"] * 100
                    to_close.append((pid, current_price, f"Трейлинг-выход Chandelier Exit (+{pnl_pct:.1f}%)"))
                    continue

            # Mega TP
            if current_price >= pos["entry"] * 1.09:
                to_close.append((pid, current_price, "Мега Тейк-Профит пампа (+9.0%)"))
                continue

            # Hard SL / Breakeven hit
            if sl and current_price <= sl:
                reason = "Безубыток (+0.2%)" if stage >= 2 and current_price >= pos["entry"] else "Стоп-лосс снайпера (-1.5%)"
                to_close.append((pid, current_price, reason))
                continue

            # Time-Stop for stalled Stage 1
            if stage == 1 and time_elapsed >= 5 * 3600:
                to_close.append((pid, current_price, "Таймаут удержания (Time-Stop 5ч)"))
                continue

        # Close flagged positions
        for pid, exit_px, reason in to_close:
            res = self.portfolio.close_position(pid, exit_px, reason)
            if res:
                emoji = "🟢" if res["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>[HYPERCASCADE — СДЕЛКА ЗАКРЫТА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{res['symbol']}</code>\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Средний вход:</b> <code>${res['entry']:.4f}</code> ➔ <b>Выход:</b> <code>${res['exit']:.4f}</code>\n"
                    f"💵 <b>Чистый PnL:</b> <code>{res['pnl']:+.2f}$</code> (<code>{res['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Банк стратегии:</b> <code>${self.portfolio.cash:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")

        # 2. Check if already in position for this symbol
        if any(p["symbol"] == symbol for p in self.portfolio.positions.values()):
            return

        if self.portfolio.cash < self.stage1_size:
            return

        # 3. Check for Explosive Breakout + Volume Spike
        is_breakout, vol_ratio, _ = self.detect_breakout_and_volume(candles)
        if is_breakout:
            sl = round(current_price * (1.0 - self.initial_sl_pct), 4)
            tp = round(current_price * 1.09, 4)

            trade_id = self.portfolio.open_position(
                symbol=symbol,
                side="BUY",
                price=current_price,
                amount_usd=self.stage1_size,
                stop_loss=sl,
                take_profit=tp,
                strategy_detail=f"HyperCascade Breakout (Vol={vol_ratio:.1f}x)"
            )
            if trade_id:
                # Add stage metadata to position
                self.portfolio.positions[trade_id]["stage"] = 1
                self.portfolio.positions[trade_id]["highest_price"] = current_price
                msg = (
                    f"💥 <b>[HYPERCASCADE — ВХОД 1-ГО УРОВНЯ]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{symbol}</code> (BUY)\n"
                    f"⚡ <b>Всплеск объема:</b> <code>{vol_ratio:.1f}x</code> выше нормы + пробой диапазона\n"
                    f"💵 <b>Стартовый объем:</b> <code>${self.stage1_size:.2f}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${current_price:.4f}</code> | 🛡 <b>SL:</b> <code>${sl:.4f}</code> (-1.5%)\n"
                    f"📈 <b>План пирамидинга:</b> Добор на +1.8% (с переводом в $0 риска) и +3.5%"
                )
                await notifier.send_message(msg, parse_mode="HTML")
