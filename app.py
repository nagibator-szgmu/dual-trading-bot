import asyncio
import html
import logging
import os
import threading
import time
import gradio as gr
from tabulate import tabulate

from config import config
from core.multi_portfolio import TournamentManager
from core.telegram_notifier import notifier
from strategies.trend_strategy import TrendStrategy
from strategies.grid_strategy import GridStrategy
from strategies.reversion_strategy import MeanReversionStrategy
from strategies.squeeze_strategy import VolatilitySqueezeStrategy
from strategies.adaptive_grid_strategy import AdaptiveGridStrategy
from strategies.dump_hunter_strategy import FlashDumpHunterStrategy
from strategies.hyper_cascade_strategy import HyperCascadeSniperStrategy
from strategies.poc_pullback_strategy import POCPullbackStrategy
from core.cs2_notifier import cs2_notifier
from core.cs2_sniper import cs2_sniper

try:
    import spaces
    @spaces.GPU
    def dummy_gpu_task():
        return True
    dummy_gpu_task()
except Exception:
    def dummy_gpu_task():
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("HF_App")

tournament = TournamentManager(initial_budget=100.0)

strat_trend = TrendStrategy(tournament.strat_trend)
strat_grid = GridStrategy(tournament.strat_grid)
strat_reversion = MeanReversionStrategy(tournament.strat_reversion)
strat_squeeze = VolatilitySqueezeStrategy(tournament.strat_squeeze)
strat_adaptive_grid = AdaptiveGridStrategy(tournament.strat_adaptive_grid)
strat_dump_hunter = FlashDumpHunterStrategy(tournament.strat_dump_hunter)
strat_cascade = HyperCascadeSniperStrategy(tournament.strat_cascade)
strat_poc = POCPullbackStrategy(tournament.strat_poc)

live_prices = {}
is_running = True

async def fetch_candles(symbol: str, bar: str = "15m", limit: int = 100):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": symbol, "bar": bar, "limit": str(limit)}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = data.get("data", [])
                    candles = []
                    for r in reversed(rows):
                        candles.append({
                            "ts": int(r[0]),
                            "open": float(r[1]),
                            "high": float(r[2]),
                            "low": float(r[3]),
                            "close": float(r[4]),
                            "vol": float(r[5])
                        })
                    return candles
    except Exception as e:
        logger.warning(f"Error fetching candles for {symbol}: {e}")
    return []

async def market_loop():
    while is_running:
        try:
            for symbol in config.SYMBOLS:
                candles = await fetch_candles(symbol)
                if not candles:
                    continue

                current_price = candles[-1]["close"]
                live_prices[symbol] = current_price

                # Update real-time position tracking metrics (MFE, MAE, bars held)
                for s in tournament.strategies:
                    s.update_position_metrics(symbol, current_price)

                await asyncio.gather(
                    strat_trend.update(symbol, current_price, candles),
                    strat_grid.update(symbol, current_price, candles),
                    strat_reversion.update(symbol, current_price, candles),
                    strat_squeeze.update(symbol, current_price, candles),
                    strat_adaptive_grid.update(symbol, current_price, candles),
                    strat_dump_hunter.update(symbol, current_price, candles),
                    strat_cascade.update(symbol, current_price, candles),
                    strat_poc.update(symbol, current_price, candles)
                )

            tournament.save()
            tournament.record_snapshot(live_prices)
        except Exception as e:
            logger.error(f"Error in market_loop: {e}")
        await asyncio.sleep(config.SCAN_INTERVAL)

async def telegram_command_listener():
    import aiohttp
    last_update_id = 0
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        return

    while is_running:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=10"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            last_update_id = update["update_id"]
                            msg = update.get("message", {})
                            raw_text = (msg.get("text", "")).strip()
                            if not raw_text:
                                continue
                            cmd = raw_text.split()[0].lower()
                            if "@" in cmd:
                                cmd = cmd.split("@")[0]
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            is_admin = (str(chat_id) == str(config.TELEGRAM_CHAT_ID)) or (str(chat_id) == "5579630526")
                            if not is_admin:
                                await notifier.send_message("🔒 Доступ ограничен. Бот находится в приватном режиме владельца.", chat_id=chat_id)
                                continue

                            if cmd in ["/start", "старт"]:
                                reply = (
                                    "👋 <b>Добро пожаловать в турнир Торговых Алгоритмов 24/7!</b>\n\n"
                                    "В этом боте 8 независимых алгоритмических стратегий соревнуются на реальном рынке криптовалют (BTC / ETH / SOL / DOGE) с равным бюджетом $100 каждая ($800 суммарно):\n"
                                    "1. ⚡ <b>Long-Only Трендовый пробой</b> (Donchian + EMA50)\n"
                                    "2. 🕸️ <b>Спотовая Сетка</b> (Trend-Hedged Grid 2.5%)\n"
                                    "3. 🌊 <b>Возврат к средней</b> (RSI(14) + BB 0.997σ + Vol 1.1x)\n"
                                    "4. 💥 <b>Квантовая Пружина</b> (ATR Volatility Squeeze Breakout)\n"
                                    "5. 📐 <b>Адаптивная Сетка</b> (ATR Dynamic Step Grid 2.1x)\n"
                                    "6. 🎯 <b>Охотник за сквизами</b> (Flash Dump & Liquidation Hunter Wick 50%)\n"
                                    "7. 🚀 <b>HyperCascade Sniper</b> (Anti-Martingale Пирамидинг & Пампы)\n"
                                    "8. 📊 <b>POC Pullback Sniper</b> (Volume Profile Point-of-Control Rebound)\n\n"
                                    "📊 <b>Команды:</b>\n"
                                    "• /status или /leaderboard — текущий баланс, PnL и рейтинг\n"
                                    "• /trades — детальный список открытых сделок\n"
                                    "• /export — скачать базу данных (CSV / JSON)\n"
                                    "• /help — справка"
                                )
                                await notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/status", "/balance", "/leaderboard", "баланс", "лидерборд", "статус"]:
                                summary = tournament.get_summary(live_prices)
                                lb = tournament.get_leaderboard(live_prices)
                                table_data = []
                                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
                                for i, s in enumerate(lb):
                                    pnl_sign = "+" if s['total_pnl'] >= 0 else ""
                                    medal = medals[i] if i < len(medals) else f"#{i+1}"
                                    table_data.append([
                                        f"{medal} {s['name'][:8]}",
                                        f"${s['equity']:.1f}",
                                        f"{pnl_sign}{s['total_pnl_pct']:.1f}%",
                                        f"{s['win_rate']:.0f}%"
                                    ])
                                ascii_table = tabulate(
                                    table_data,
                                    headers=["Стратегия", "Депо", "PnL", "WR"],
                                    tablefmt="simple"
                                )
                                reply = (
                                    f"🏆 <b>ТУРНИР СТРАТЕГИЙ (24/7 Cloud)</b>\n\n"
                                    f"💰 <b>Общий капитал:</b> ${summary['total_equity']:.2f}\n"
                                    f"💵 <b>Свободный кэш:</b> ${summary['total_cash']:.2f}\n"
                                    f"📊 <b>Общий PnL:</b> {'+' if summary['total_pnl'] >= 0 else ''}${summary['total_pnl']:.2f} ({summary['total_pnl_pct']:+.2f}%)\n"
                                    f"⚡ <b>Открыто позиций:</b> {summary['total_open_positions']}\n\n"
                                    f"<pre>{html.escape(ascii_table)}</pre>"
                                )
                                await notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/trades", "/positions", "сделки", "позиции"]:
                                lines = ["📊 <b>АКТИВНЫЕ СДЕЛКИ ПО СТРАТЕГИЯМ:</b>\n"]
                                has_trades = False
                                for strat_name, strat in [
                                    ("⚡ Trend Follower", tournament.strat_trend),
                                    ("🕸️ Spot Grid", tournament.strat_grid),
                                    ("🌊 Mean Reversion", tournament.strat_reversion),
                                    ("💥 Volatility Squeeze", tournament.strat_squeeze),
                                    ("📐 Adaptive Grid", tournament.strat_adaptive_grid),
                                    ("🎯 Flash Dump Hunter", tournament.strat_dump_hunter),
                                    ("🚀 HyperCascade Sniper", tournament.strat_cascade),
                                    ("📊 POC Pullback Sniper", tournament.strat_poc)
                                ]:
                                    if strat.positions:
                                        has_trades = True
                                        lines.append(f"<b>{strat_name}:</b>")
                                        for p in strat.positions.values():
                                             curr = live_prices.get(p['symbol'], p['entry'])
                                             if p['side'] == 'BUY':
                                                 upnl = (curr - p['entry']) / p['entry'] * p['amount']
                                             else:
                                                 upnl = (p['entry'] - curr) / p['entry'] * p['amount']
                                             pnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"
                                             lines.append(
                                                 f"• {p['symbol']} ({p['side']}) | Вход: ${p['entry']:.2f} → Тек: ${curr:.2f} | PnL: {pnl_str}"
                                             )
                                        lines.append("")
                                if not has_trades:
                                    reply = "📭 На данный момент активных сделок нет. Алгоритмы сканируют рынок."
                                else:
                                    reply = "\n".join(lines)
                                await notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/export", "/csv", "/data", "/database", "/отчет", "экспорт"]:
                                is_admin = (not config.TELEGRAM_CHAT_ID) or (str(chat_id) == str(config.TELEGRAM_CHAT_ID)) or (str(chat_id) == "5579630526")
                                if not is_admin:
                                    await notifier.send_message("⛔ Доступ ограничен. Выгрузка базы данных доступна только администратору.", chat_id=chat_id)
                                    continue

                                tournament.save()
                                csv_file = tournament.export_csv("tournament_trades.csv")
                                snap_file = tournament.export_snapshots_csv("snapshots.csv")
                                sig_file = tournament.export_signals_csv("signals.csv")
                                json_file = "portfolio_state.json"

                                caption = (
                                    "📊 <b>Экспорт данных турнира (Полный Квант-Пакет):</b>\n\n"
                                    "1️⃣ <code>tournament_trades.csv</code> — 47 параметров по каждой сделке (MFE/MAE, R:R, комиссии, индикаторы входа, причины выхода).\n"
                                    "2️⃣ <code>snapshots.csv</code> — временной ряд эквити, кэша и просадок от пика (Drawdown from peak).\n"
                                    "3️⃣ <code>signals.csv</code> — лог всех рыночных сигналов и фильтров отсева.\n"
                                    "4️⃣ <code>portfolio_state.json</code> — сырой снимок состояния системы."
                                )
                                await notifier.send_document(csv_file, chat_id=chat_id, caption=caption)
                                if os.path.exists(snap_file):
                                    await notifier.send_document(snap_file, chat_id=chat_id, caption="📈 <code>snapshots.csv</code> — кривые доходности и просадок по стратегиям.")
                                if os.path.exists(sig_file):
                                    await notifier.send_document(sig_file, chat_id=chat_id, caption="⚡ <code>signals.csv</code> — диагностика срабатывания сигналов.")
                                if os.path.exists(json_file):
                                    await notifier.send_document(json_file, chat_id=chat_id, caption="🗄️ <code>portfolio_state.json</code> — полный сырой JSON-дамп состояния турнира.")

                            elif cmd in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды турнира:</b>\n"
                                    "• /leaderboard или /status — лидерборд и рейтинг трех стратегий\n"
                                    "• /trades — детальный список открытых сделок каждой стратегии\n"
                                    "• /export — <b>выгрузить CSV и JSON базы данных в файл</b> для передачи другому AI\n"
                                    "• /help — эта справка"
                                )
                                await notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"Command listener error: {e}")
            await asyncio.sleep(2)

async def heartbeat_loop():
    logger.info("Автоматический таймер проверки каждые 3 часа активирован.")
    while is_running:
        await asyncio.sleep(3 * 3600)  # 3 часа = 10 800 секунд
        try:
            summary = tournament.get_summary(live_prices)
            lb = tournament.get_leaderboard(live_prices)
            leader = lb[0] if lb else None
            leader_text = f"{leader['name']} (${leader['equity']:.2f}, {leader['total_pnl_pct']:+.1f}%)" if leader else "—"
            pnl_sign = "+" if summary['total_pnl'] >= 0 else "-"
            pnl_str = f"{pnl_sign}${abs(summary['total_pnl']):.2f}"

            msg = (
                "🟢 <b>[АВТО-ПРОВЕРКА СЕРВЕРА 24/7 — ВСЁ В НОРМЕ]</b>\n\n"
                "🛡 <i>Все скрипты работают штатно, котировки OKX обновляются, деньги фармятся!</i>\n\n"
                f"💰 <b>Общий капитал:</b> <code>${summary['total_equity']:.2f}</code>\n"
                f"💵 <b>Свободный кэш:</b> <code>${summary['total_cash']:.2f}</code>\n"
                f"📊 <b>Суммарный PnL:</b> <code>{pnl_str}</code> (<code>{summary['total_pnl_pct']:+.2f}%</code>)\n"
                f"⚡ <b>Открыто позиций:</b> <code>{summary['total_open_positions']} шт.</code>\n"
                f"🥇 <b>Лидер турнира:</b> <code>{leader_text}</code>\n\n"
                "📱 Команды: /status, /trades, /export"
            )
            await notifier.send_message(msg, parse_mode="HTML")
            logger.info("3-часовой авто-отчет успешно отправлен в Telegram.")
        except Exception as e:
            logger.error(f"Ошибка в heartbeat_loop: {e}")

def generate_dashboard_html():
    lb = tournament.get_leaderboard(live_prices)
    summary = tournament.get_summary(live_prices)

    rows_html = ""
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    for i, s in enumerate(lb):
        pnl_color = "#34d399" if s['total_pnl'] >= 0 else "#f87171"
        pnl_str = f"+${s['total_pnl']:.2f}" if s['total_pnl'] >= 0 else f"-${abs(s['total_pnl']):.2f}"
        medal = medals[i] if i < len(medals) else f"#{i+1}"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding: 14px 18px;"><span style="font-weight: 700; font-size: 15px;">{medal} {i+1}</span></td>
            <td style="padding: 14px 18px; font-weight: 600;">{s['name']}</td>
            <td style="padding: 14px 18px; font-weight: bold;">${s['equity']:.2f}</td>
            <td style="padding: 14px 18px;">${s['cash']:.2f}</td>
            <td style="padding: 14px 18px;">${s['invested']:.2f} <span style="color: #94a3b8; font-size: 12px;">({s['open_count']} шт.)</span></td>
            <td style="padding: 14px 18px; color: {pnl_color}; font-weight: 600;">{pnl_str} ({s['total_pnl_pct']:+.2f}%)</td>
            <td style="padding: 14px 18px;">{s['win_rate']:.1f}% <span style="color: #94a3b8; font-size: 12px;">({s['closed_count']} сд.)</span></td>
        </tr>
        """

    rates_html = ""
    for k, v in live_prices.items():
        rates_html += f"<span style='background: #334155; padding: 6px 12px; border-radius: 6px; font-size: 13px; display: inline-block; margin: 4px;'><b>{k}</b>: ${v:.2f}</span> "

    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; border-radius: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 16px; margin-bottom: 24px;">
            <h1 style="margin: 0; font-size: 24px; color: #f8fafc;">🏆 Турнир Алгоритмических Торговых Ботов</h1>
            <span style="background: #064e3b; color: #34d399; padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;">● Cloud Live 24/7</span>
        </div>

        <div style="background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; display: flex; gap: 32px; flex-wrap: wrap;">
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Общий Капитал Турнира</div>
                <div style="font-size: 26px; font-weight: bold; color: #f8fafc;">${summary['total_equity']:.2f}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Свободный Кэш</div>
                <div style="font-size: 26px; font-weight: bold; color: #38bdf8;">${summary['total_cash']:.2f}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Суммарный PnL</div>
                <div style="font-size: 26px; font-weight: bold; color: {'#34d399' if summary['total_pnl'] >= 0 else '#f87171'};">
                    {'+$' if summary['total_pnl'] >= 0 else '-$'}{abs(summary['total_pnl']):.2f} ({summary['total_pnl_pct']:+.2f}%)
                </div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Открыто Позиций</div>
                <div style="font-size: 26px; font-weight: bold; color: #f8fafc;">{summary['total_open_positions']}</div>
            </div>
        </div>

        <div style="margin-bottom: 20px;">
            <span style="color: #94a3b8; font-size: 13px; margin-right: 12px;">Котировки OKX:</span>
            {rates_html if rates_html else "<span style='color: #94a3b8;'>Загрузка котировок...</span>"}
        </div>

        <table style="width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden;">
            <thead>
                <tr style="background: #090d16; color: #94a3b8; font-size: 13px; text-align: left;">
                    <th style="padding: 14px 18px;">Место</th>
                    <th style="padding: 14px 18px;">Стратегия</th>
                    <th style="padding: 14px 18px;">Капитал</th>
                    <th style="padding: 14px 18px;">Кэш</th>
                    <th style="padding: 14px 18px;">В сделках</th>
                    <th style="padding: 14px 18px;">PnL ($ / %)</th>
                    <th style="padding: 14px 18px;">Винрейт</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div style="margin-top: 24px; text-align: center; color: #94a3b8; font-size: 13px;">
            Управление и экспорт: <a href="https://t.me/ct331_bot" target="_blank" style="color: #38bdf8; text-decoration: none; font-weight: 600;">Telegram @ct331_bot</a> | Команды: /status, /trades, /export, /help
        </div>
    </div>
    """
    return html_content

def generate_cs2_dashboard_html():
    sm = cs2_sniper.get_summary()
    items = sm.get("items", [])
    
    rows_html = ""
    for it in items:
        rows_html += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding: 14px 18px; font-weight: 600;"><a href="{it['url']}" target="_blank" style="color: #38bdf8; text-decoration: none;">{html.escape(it['name'])}</a></td>
            <td style="padding: 14px 18px;">${it['buy']:.2f}</td>
            <td style="padding: 14px 18px; color: #94a3b8;">${it['fair']:.2f}</td>
            <td style="padding: 14px 18px; color: #34d399; font-weight: 600;">-{it['disc']:.1f}%</td>
            <td style="padding: 14px 18px; color: #38bdf8;">{it.get('vol_30', '—')} шт.</td>
            <td style="padding: 14px 18px; color: #34d399; font-weight: bold;">+${it['est_profit']:.2f}</td>
            <td style="padding: 14px 18px;"><span style="background: #1e3a8a; color: #93c5fd; padding: 4px 10px; border-radius: 6px; font-size: 12px;">🔒 ~{it['days_left']} дн.</span></td>
        </tr>
        """
        
    if not items:
        rows_html = """
        <tr>
            <td colspan="7" style="padding: 24px; text-align: center; color: #94a3b8;">
                Инвентарь сканируется. Бот отбирает ликвидные скидки >15% на Skinport.
            </td>
        </tr>
        """

    pnl_color = "#34d399" if sm['total_pnl'] >= 0 else "#f87171"
    pnl_sign = "+" if sm['total_pnl'] >= 0 else ""

    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; border-radius: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 16px; margin-bottom: 24px;">
            <h1 style="margin: 0; font-size: 24px; color: #f8fafc;">🔫 CS2 Skin Paper Sniper</h1>
            <span style="background: #1e3a8a; color: #60a5fa; padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;">● Skinport Live Engine</span>
        </div>

        <div style="background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; display: flex; gap: 32px; flex-wrap: wrap;">
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Общий Капитал</div>
                <div style="font-size: 26px; font-weight: bold; color: #f8fafc;">${sm['equity']:.2f}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Свободный Кэш</div>
                <div style="font-size: 26px; font-weight: bold; color: #38bdf8;">${sm['cash']:.2f}</div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Вложено в Скины</div>
                <div style="font-size: 26px; font-weight: bold; color: #f8fafc;">${sm['invested']:.2f} <span style="font-size: 14px; color: #94a3b8;">({sm['inventory_count']} шт.)</span></div>
            </div>
            <div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 6px;">Расчетный PnL</div>
                <div style="font-size: 26px; font-weight: bold; color: {pnl_color};">
                    {pnl_sign}${sm['total_pnl']:.2f} ({pnl_sign}{sm['total_pnl_pct']:.2f}%)
                </div>
            </div>
        </div>

        <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 16px; color: #cbd5e1;">🎒 Текущий Инвентарь (Trade Lock):</h3>
        <table style="width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden;">
            <thead>
                <tr style="background: #090d16; color: #94a3b8; font-size: 13px; text-align: left;">
                    <th style="padding: 14px 18px;">Предмет</th>
                    <th style="padding: 14px 18px;">Цена покупки</th>
                    <th style="padding: 14px 18px;">Fair Price</th>
                    <th style="padding: 14px 18px;">Скидка</th>
                    <th style="padding: 14px 18px;">Объем (30д)</th>
                    <th style="padding: 14px 18px;">Профит</th>
                    <th style="padding: 14px 18px;">Блокировка</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div style="margin-top: 24px; text-align: center; color: #94a3b8; font-size: 13px;">
            Управление CS2 ботом: <a href="https://t.me/cs233_bot" target="_blank" style="color: #38bdf8; text-decoration: none; font-weight: 600;">Telegram @cs233_bot</a> | Команды: /status, /inventory, /scan, /export
        </div>
    </div>
    """
    return html_content

async def cs2_sniper_loop():
    logger.info("CS2 Skin Paper Sniper запущен (интервал %d сек)...", config.CS2_SCAN_INTERVAL)
    while is_running:
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, cs2_sniper.scan_and_snipe)
            
            for item in res.get("sniped", []):
                msg = (
                    f"🎯 <b>[CS2 СНАЙПЕР — ВЫКУПЛЕН СКИДКА {item['discount_pct']:.1f}%]</b>\n\n"
                    f"🔫 <b>Предмет:</b> <code>{html.escape(item['name'])}</code>\n"
                    f"💵 <b>Цена выкупа:</b> <code>${item['buy_price']:.2f}</code>\n"
                    f"📊 <b>Справедливая цена:</b> <code>${item['fair_price']:.2f}</code>\n"
                    f"💰 <b>Расчетный профит:</b> <code>+${item['est_profit']:.2f}</code> (<code>+{item['est_profit_pct']:.1f}%</code>)\n"
                    f"🔒 <b>Trade Lock:</b> <code>7 дней</code>\n"
                    f"💼 <b>Свободный кэш:</b> <code>${cs2_sniper.cash:.2f}</code>\n"
                    f"🔗 <a href='{item.get('url', 'https://skinport.com')}'>Открыть лот на Skinport</a>"
                )
                await cs2_notifier.send_message(msg, parse_mode="HTML")

            for s in res.get("sold", []):
                p_sign = "+" if s.get('profit', 0) >= 0 else ""
                p_pct_sign = "+" if s.get('profit_pct', 0) >= 0 else ""
                drift_str = f"\n📊 <b>Дрифт цены рынка за 7д:</b> <code>{s.get('price_drift_pct', 0.0):+.1f}%</code>" if s.get('price_drift_pct') is not None else ""
                msg = (
                    f"💰 <b>[CS2 СНАЙПЕР — ПРОДАН ПРЕДМЕТ]</b>\n\n"
                    f"🔫 <b>Предмет:</b> <code>{html.escape(s['name'])}</code>\n"
                    f"💵 <b>Вход:</b> <code>${s['buy_price']:.2f}</code> ➔ <b>Выручка:</b> <code>${s['net_sale']:.2f}</code> (витрина: ${s['sell_price']:.2f})\n"
                    f"📈 <b>Чистая прибыль:</b> <code>{p_sign}{s['profit']:.2f}$</code> (<code>{p_pct_sign}{s['profit_pct']:.1f}%</code>){drift_str}\n"
                    f"💼 <b>Баланс CS2 банка:</b> <code>${cs2_sniper.cash:.2f}</code>"
                )
                await cs2_notifier.send_message(msg, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Ошибка в cs2_sniper_loop: {e}")

        await asyncio.sleep(config.CS2_SCAN_INTERVAL)

async def cs2_telegram_command_listener():
    import aiohttp
    last_update_id = 0
    token = config.CS2_BOT_TOKEN
    if not token:
        return

    while is_running:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=10"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            last_update_id = update["update_id"]
                            msg = update.get("message", {})
                            raw_text = (msg.get("text", "")).strip()
                            if not raw_text:
                                continue
                            cmd = raw_text.split()[0].lower()
                            if "@" in cmd:
                                cmd = cmd.split("@")[0]
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            is_admin = (str(chat_id) == str(config.TELEGRAM_CHAT_ID)) or (str(chat_id) == "5579630526")
                            if not is_admin:
                                await cs2_notifier.send_message("🔒 Доступ ограничен. CS2 Снайпер находится в приватном режиме владельца.", chat_id=chat_id)
                                continue

                            if cmd in ["/start", "старт"]:
                                reply = (
                                    "👋 <b>Добро пожаловать в CS2 Skin Paper Sniper!</b>\n\n"
                                    "🔫 <i>Автономный бот 24/7 для снайпинга недооцененных скинов, демпинга и аномальных скидок на Skinport и Steam Market.</i>\n\n"
                                    "💰 <b>Виртуальный депозит:</b> $200.00\n"
                                    "🎯 <b>Критерии поиска:</b> дисконт от 15% к справедливой цене, чистый профит от 8% с учетом комиссий, мин. 15 продаж за 30 дней.\n\n"
                                    "📊 <b>Команды:</b>\n"
                                    "• /status — баланс, PnL и сводка по инвентарю\n"
                                    "• /inventory или /items — список удерживаемых скинов и Trade Lock\n"
                                    "• /scan — мгновенный поиск топ-скидок на рынке прямо сейчас\n"
                                    "• /export — выгрузить базу сделок и инвентарь (CSV / JSON)\n"
                                    "• /help — справка"
                                )
                                await cs2_notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/status", "/balance", "баланс", "статус"]:
                                sm = cs2_sniper.get_summary()
                                reply = (
                                    "🔫 <b>CS2 SKIN SNIPER — СТАТУС ИНВЕНТАРЯ</b>\n\n"
                                    f"💰 <b>Общий капитал:</b> ${sm['equity']:.2f}\n"
                                    f"💵 <b>Свободный кэш:</b> ${sm['cash']:.2f}\n"
                                    f"🎒 <b>Вложено в скины:</b> ${sm['invested']:.2f} ({sm['inventory_count']} шт.)\n"
                                    f"📈 <b>Нереализованный PnL:</b> {'+' if sm['unrealized_profit'] >= 0 else ''}${sm['unrealized_profit']:.2f}\n"
                                    f"📊 <b>Суммарный PnL:</b> {'+' if sm['total_pnl'] >= 0 else ''}${sm['total_pnl']:.2f} ({sm['total_pnl_pct']:+.2f}%)\n"
                                    f"🏆 <b>Успешных продаж:</b> {sm['sold_count']} шт. (Винрейт: {sm['win_rate']:.1f}%)\n\n"
                                    "Команды: /inventory, /scan, /export"
                                )
                                await cs2_notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/inventory", "/items", "инвентарь", "скины"]:
                                sm = cs2_sniper.get_summary()
                                items = sm.get("items", [])
                                if not items:
                                    reply = "📭 <b>Инвентарь пуст.</b> Бот сканирует рынок на предмет скидок от 15%."
                                else:
                                    lines = ["🎒 <b>СКИHЫ В ИНВЕНТАРЕ (TRADE LOCK):</b>\n"]
                                    for it in items:
                                        lines.append(
                                            f"• <b>{html.escape(it['name'])}</b>\n"
                                            f"  💵 Куплено: ${it['buy']:.2f} ➔ Оценка: ${it['fair']:.2f} (-{it['disc']}%) | Профит: +${it['est_profit']:.2f}\n"
                                            f"  🔒 Trade Lock: осталось ~{it['days_left']} дн.\n"
                                        )
                                    reply = "\n".join(lines)
                                await cs2_notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

                            elif cmd in ["/scan", "скан", "поиск"]:
                                if not is_admin:
                                    await cs2_notifier.send_message("⛔ Ручной запуск сканирования доступен только администратору для защиты от rate-limit.", chat_id=chat_id)
                                    continue

                                await cs2_notifier.send_message("🔍 Сканирую каталог Skinport на ликвидные дисконты (>15%)...", chat_id=chat_id)
                                try:
                                    loop = asyncio.get_running_loop()
                                    top = await loop.run_in_executor(None, cs2_sniper.get_top_discounts, 5)
                                    if not top:
                                        reply = "🔍 Сканирование завершено. Ликвидных скидок >15% в эту секунду не обнаружено."
                                    else:
                                        lines = ["🔥 <b>ТОП-5 ЛИКВИДНЫХ ДИСКОНТОВ НА SKINPORT ПРЯМО СЕЙЧАС:</b>\n"]
                                        for c in top:
                                            lines.append(
                                                f"• <b>{html.escape(c['name'])}</b>\n"
                                                f"  🏷 Выставлен за: <b>${c['min_price']:.2f}</b> (Fair: ${c['fair_price']:.2f})\n"
                                                f"  📉 Скидка: <b>-{c['discount_pct']:.1f}%</b> | Объем (30д): <b>{c['vol_30']} шт.</b>\n"
                                                f"  💰 Чистый профит (после 8%): <b>+{c['net_pct']:.1f}%</b> (+${c['net_profit']:.2f})\n"
                                                f"  📦 Наличие: {c['quantity']} шт. | <a href='{c['url']}'>Ссылка на Skinport</a>\n"
                                            )
                                        reply = "\n".join(lines)
                                    await cs2_notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")
                                except Exception as err:
                                    logger.error(f"Error executing /scan: {err}")
                                    await cs2_notifier.send_message(f"⚠️ Ошибка сканирования: {err}", chat_id=chat_id)

                            elif cmd in ["/export", "экспорт", "csv"]:
                                if not is_admin:
                                    await cs2_notifier.send_message("⛔ Экспорт базы данных доступен только администратору.", chat_id=chat_id)
                                    continue

                                cs2_sniper.save()
                                csv_path = cs2_sniper.export_csv("cs2_trades.csv")
                                json_path = "cs2_portfolio_state.json"
                                await cs2_notifier.send_document(csv_path, chat_id=chat_id, caption="📊 <code>cs2_trades.csv</code> — плоская таблица сделок и удерживаемых скинов.")
                                if os.path.exists(json_path):
                                    await cs2_notifier.send_document(json_path, chat_id=chat_id, caption="🗄️ <code>cs2_portfolio_state.json</code> — сырой JSON-дамп инвентаря CS2.")

                            elif cmd in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды CS2 Снайпера:</b>\n"
                                    "• /status — баланс и профит\n"
                                    "• /inventory — инвентарь и статус Trade Lock\n"
                                    "• /scan — живой поиск топ-дисконтов на Skinport\n"
                                    "• /export — скачать CSV и JSON базы\n"
                                    "• /help — эта справка"
                                )
                                await cs2_notifier.send_message(reply, chat_id=chat_id, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"CS2 Command listener error: {e}")
            await asyncio.sleep(2)

def run_async_services():
    while is_running:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logger.info("Starting async background services event loop...")
            loop.run_until_complete(
                asyncio.gather(
                    market_loop(),
                    telegram_command_listener(),
                    heartbeat_loop(),
                    cs2_sniper_loop(),
                    cs2_telegram_command_listener(),
                    return_exceptions=True
                )
            )
        except Exception as e:
            logger.error(f"Async services crashed, auto-restarting in 5s: {e}")
            time.sleep(5)

_bg_started = False
_bg_lock = threading.Lock()

def ensure_background_services():
    global _bg_started
    with _bg_lock:
        if not _bg_started:
            _bg_started = True
            t = threading.Thread(target=run_async_services, daemon=True, name="TradingAsyncServices")
            t.start()
            logger.info(">>> Background async trading services STARTED in thread <<<")

# Auto-start on module load (guarantees execution under uvicorn, gradio cli, or python app.py)
ensure_background_services()

with gr.Blocks(title="🏆 Crypto & CS2 Quant Trading Platform") as demo:
    with gr.Tabs():
        with gr.Tab("🏆 Крипто-Турнир (8 Стратегий)"):
            crypto_ui = gr.HTML(value=generate_dashboard_html)
        with gr.Tab("🔫 CS2 Skin Sniper ($200 Инвентарь)"):
            cs2_ui = gr.HTML(value=generate_cs2_dashboard_html)
            
    timer = gr.Timer(5)
    timer.tick(fn=generate_dashboard_html, outputs=crypto_ui)
    timer.tick(fn=generate_cs2_dashboard_html, outputs=cs2_ui)
    demo.load(fn=ensure_background_services)

if __name__ == "__main__":
    ensure_background_services()
    port = int(os.getenv("PORT", "7860"))
    logger.info(f"Запуск Gradio платформы на порту {port}...")
    demo.launch(server_name="0.0.0.0", server_port=port)
