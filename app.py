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

try:
    import spaces
    @spaces.GPU
    def dummy_gpu_task():
        return True
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("HF_App")

tournament = TournamentManager(
    trend_budget=config.TREND_BUDGET,
    grid_budget=config.GRID_BUDGET,
    reversion_budget=config.REVERSION_BUDGET
)

strat_trend = TrendStrategy(tournament.strat_trend)
strat_grid = GridStrategy(tournament.strat_grid)
strat_reversion = MeanReversionStrategy(tournament.strat_reversion)

live_prices = {}
is_running = True

async def fetch_candles(symbol: str, bar: str = "15m", limit: int = 40):
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
        for symbol in config.SYMBOLS:
            candles = await fetch_candles(symbol)
            if not candles:
                continue

            current_price = candles[-1]["close"]
            live_prices[symbol] = current_price

            await asyncio.gather(
                strat_trend.update(symbol, current_price, candles),
                strat_grid.update(symbol, current_price, candles),
                strat_reversion.update(symbol, current_price, candles)
            )

        tournament.save()
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
                            text = (msg.get("text", "")).strip().lower()
                            chat_id = str(msg.get("chat", {}).get("id", ""))

                            if chat_id != str(config.TELEGRAM_CHAT_ID):
                                continue

                            if text in ["/status", "/balance", "/leaderboard"]:
                                summary = tournament.get_summary(live_prices)
                                lb = tournament.get_leaderboard(live_prices)
                                table_data = []
                                medals = ["🥇", "🥈", "🥉"]
                                for i, s in enumerate(lb):
                                    pnl_sign = "+" if s['total_pnl'] >= 0 else ""
                                    table_data.append([
                                        f"{medals[i]} {s['name'][:8]}",
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
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/trades", "/positions", "сделки"]:
                                lines = ["📊 <b>АКТИВНЫЕ СДЕЛКИ ПО СТРАТЕГИЯМ:</b>\n"]
                                has_trades = False
                                for strat_name, strat in [
                                    ("⚡ Trend Follower", tournament.strat_trend),
                                    ("🕸️ Spot Grid", tournament.strat_grid),
                                    ("🌊 Mean Reversion", tournament.strat_reversion)
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
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды турнира:</b>\n"
                                    "• /leaderboard или /status — лидерборд и рейтинг трех стратегий\n"
                                    "• /trades — детальный список открытых сделок каждой стратегии\n"
                                    "• /help — эта справка"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"Command listener error: {e}")
            await asyncio.sleep(2)

def generate_dashboard_html():
    lb = tournament.get_leaderboard(live_prices)
    summary = tournament.get_summary(live_prices)

    rows_html = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, s in enumerate(lb):
        pnl_color = "#34d399" if s['total_pnl'] >= 0 else "#f87171"
        pnl_str = f"+${s['total_pnl']:.2f}" if s['total_pnl'] >= 0 else f"-${abs(s['total_pnl']):.2f}"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding: 14px 18px;"><span style="font-weight: 700; font-size: 15px;">{medals[i]} {i+1}</span></td>
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
            Управление и живые сигналы: <a href="https://t.me/bu7634bot" target="_blank" style="color: #38bdf8; text-decoration: none; font-weight: 600;">Telegram @bu7634bot</a> | Команды: /status, /trades, /help
        </div>
    </div>
    """
    return html_content

def run_async_services():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        asyncio.gather(
            market_loop(),
            telegram_command_listener()
        )
    )

with gr.Blocks(title="🏆 Crypto Algorithmic Tournament Bot") as demo:
    dashboard_ui = gr.HTML(value=generate_dashboard_html)
    timer = gr.Timer(5)
    timer.tick(fn=generate_dashboard_html, outputs=dashboard_ui)

if __name__ == "__main__":
    t = threading.Thread(target=run_async_services, daemon=True)
    t.start()
    port = int(os.getenv("PORT", "7860"))
    logger.info(f"Запуск Gradio турнирного дашборда на порту {port}...")
    demo.launch(server_name="0.0.0.0", server_port=port)
