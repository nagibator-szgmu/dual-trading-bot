import asyncio
import html
import logging
import os
import time
from aiohttp import web
from tabulate import tabulate

from config import config
from core.multi_portfolio import TournamentManager
from core.telegram_notifier import notifier
from strategies.trend_strategy import TrendStrategy
from strategies.grid_strategy import GridStrategy
from strategies.reversion_strategy import MeanReversionStrategy

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

                            if chat_id != config.TELEGRAM_CHAT_ID:
                                continue

                            if text in ["/status", "/balance", "/leaderboard", "баланс", "статус", "лидерборд"]:
                                lb = tournament.get_leaderboard(live_prices)
                                summary = tournament.get_summary(live_prices)
                                medals = ["🥇", "🥈", "🥉"]
                                lines = []
                                for i, s in enumerate(lb):
                                    lines.append(
                                        f"{medals[i]} <b>{s['name']}:</b>\n"
                                        f"   Капитал: <code>${s['equity']:.2f}</code> | PnL: <code>{s['total_pnl']:+.2f}$</code>\n"
                                        f"   Сделок в работе: <code>{s['open_count']}</code> | Винрейт: <code>{s['win_rate']:.1f}%</code>"
                                    )
                                reply = (
                                    f"🏆 <b>[ТУРНИР АЛГОРИТМОВ — РЕЙТИНГ]</b>\n\n"
                                    + "\n\n".join(lines) +
                                    f"\n\n💰 <b>Всего капитал (3 банка):</b> <code>${summary['total_equity']:.2f}</code> (PnL: <code>{summary['total_pnl']:+.2f}$</code>)"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/trades", "/positions", "сделки", "позиции"]:
                                lb = tournament.get_leaderboard(live_prices)
                                sections = []
                                for s in lb:
                                    pos = s["positions"]
                                    if not pos:
                                        sections.append(f"<b>{s['name']}:</b> <i>нет открытых сделок</i>")
                                    else:
                                        p_lines = []
                                        for p in pos:
                                            emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                                            p_lines.append(
                                                f"  • <b>{p['symbol']}</b> ({p['side']}): вход ${p['entry']:.2f} ➔ сейчас ${p['current']:.2f} "
                                                f"[{emoji} <code>{p['pnl']:+.2f}$</code>]"
                                            )
                                        sections.append(f"<b>{s['name']}:</b>\n" + "\n".join(p_lines))

                                reply = "📋 <b>[АКТИВНЫЕ ПОЗИЦИИ ПО СТРАТЕГИЯМ]:</b>\n\n" + "\n\n".join(sections)
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

# --- WEB DASHBOARD FOR HUGGING FACE SPACES (PORT 7860) ---
async def handle_index(request):
    lb = tournament.get_leaderboard(live_prices)
    summary = tournament.get_summary(live_prices)
    
    rows_html = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, s in enumerate(lb):
        pnl_class = "green" if s['total_pnl'] >= 0 else "red"
        pnl_str = f"+${s['total_pnl']:.2f}" if s['total_pnl'] >= 0 else f"-${abs(s['total_pnl']):.2f}"
        rows_html += f"""
        <tr>
            <td><span class="medal">{medals[i]} {i+1}</span></td>
            <td><strong>{s['name']}</strong></td>
            <td>${s['equity']:.2f}</td>
            <td>${s['cash']:.2f}</td>
            <td>${s['invested']:.2f} ({s['open_count']} шт.)</td>
            <td class="{pnl_class}">{pnl_str} ({s['total_pnl_pct']:+.2f}%)</td>
            <td>{s['win_rate']:.1f}% ({s['closed_count']} сд.)</td>
        </tr>
        """

    rates_html = ""
    for k, v in live_prices.items():
        rates_html += f"<span class='pill'><b>{k}</b>: ${v:.2f}</span> "

    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏆 Турнир Торговых Алгоритмов 24/7</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 960px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #334155;
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{ margin: 0; font-size: 24px; }}
        .status-badge {{
            background: #064e3b;
            color: #34d399;
            padding: 6px 14px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 600;
        }}
        .summary-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
            display: flex;
            gap: 32px;
        }}
        .metric-title {{ color: #94a3b8; font-size: 13px; margin-bottom: 6px; }}
        .metric-value {{ font-size: 26px; font-weight: bold; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #1e293b;
            border-radius: 12px;
            overflow: hidden;
        }}
        th, td {{
            padding: 14px 18px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        th {{ background: #0f172a; color: #94a3b8; font-size: 13px; font-weight: 600; }}
        .green {{ color: #34d399; font-weight: 600; }}
        .red {{ color: #f87171; font-weight: 600; }}
        .pill {{
            background: #334155;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            display: inline-block;
            margin: 4px;
        }}
        .tg-link {{
            color: #38bdf8;
            text-decoration: none;
            font-weight: 600;
        }}
        .footer {{
            margin-top: 24px;
            color: #64748b;
            font-size: 12px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🏆 Турнир Алгоритмов (3 стратегии по $100)</h1>
                <p style="margin: 4px 0 0; color: #94a3b8; font-size: 14px;">Автономный бот 24/7 | Обновление каждые 10 сек</p>
            </div>
            <div class="status-badge">🟢 ONLINE 24/7</div>
        </header>

        <div class="summary-card">
            <div>
                <div class="metric-title">ОБЩИЙ БАНК (3 СТРАТЕГИИ)</div>
                <div class="metric-value">${summary['total_equity']:.2f}</div>
            </div>
            <div>
                <div class="metric-title">ИТОГОВЫЙ PnL</div>
                <div class="metric-value {'green' if summary['total_pnl'] >= 0 else 'red'}">{summary['total_pnl']:+.2f}$ ({summary['total_pnl_pct']:+.2f}%)</div>
            </div>
            <div>
                <div class="metric-title">TELEGRAM БОТ</div>
                <div class="metric-value" style="font-size: 18px; padding-top: 6px;">
                    <a class="tg-link" href="https://t.me/bu7634bot" target="_blank">@bu7634bot ↗</a>
                </div>
            </div>
        </div>

        <div style="margin-bottom: 20px;">
            <span style="color: #94a3b8; font-size: 13px; margin-right: 8px;">Курсы OKX Live:</span>
            {rates_html}
        </div>

        <table>
            <thead>
                <tr>
                    <th>Место</th>
                    <th>Стратегия</th>
                    <th>Капитал</th>
                    <th>Кэш</th>
                    <th>В сделках</th>
                    <th>PnL ($ / %)</th>
                    <th>Винрейт</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="footer">
            Управление и живые сигналы: <a class="tg-link" href="https://t.me/bu7634bot" target="_blank">Telegram @bu7634bot</a> | Команды: /status, /trades, /help
        </div>
    </div>
</body>
</html>
"""
    return web.Response(text=html_content, content_type="text/html")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "7860"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web dashboard started on port {port} (Hugging Face Spaces ready)")

async def main():
    logger.info("Запуск ТУРНИРА АЛГОРИТМОВ с Web-дашбордом для Hugging Face...")
    await asyncio.gather(
        start_web_server(),
        market_loop(),
        telegram_command_listener()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
