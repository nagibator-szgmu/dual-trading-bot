import aiohttp
import asyncio
import json
import logging
import html
from typing import List, Dict, Any, Optional
from config import config
from core.paper_portfolio import PaperPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class PolymarketEngine:
    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio = portfolio
        self.gamma_url = "https://gamma-api.polymarket.com/events"
        self.seen_opportunities: set = set()

    async def fetch_active_events(self, limit: int = 35) -> List[Dict[str, Any]]:
        params = {
            "closed": "false",
            "limit": str(limit),
            "order": "volume24hr",
            "ascending": "false"
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; DualBot/1.0)"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.gamma_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.warning(f"[Polymarket] Error fetching events: {e}")
        return []

    async def check_and_settle_positions(self, events: List[Dict[str, Any]]):
        # Build map of active markets
        markets_by_slug = {}
        for ev in events:
            ev_slug = ev.get("slug", "")
            if ev_slug:
                markets_by_slug[ev_slug] = ev
            for m in ev.get("markets", []):
                m_slug = m.get("slug", "")
                if m_slug:
                    markets_by_slug[m_slug] = m

        to_settle = []
        for trade_id, pos in list(self.portfolio.poly_positions.items()):
            slug = pos.get("slug", "")
            outcome = pos.get("outcome", "")
            m_data = markets_by_slug.get(slug)
            if not m_data:
                continue

            # Check if market has prices
            prices_raw = m_data.get("outcomePrices")
            outcomes_raw = m_data.get("outcomes")
            is_closed = m_data.get("closed", False)
            
            if not prices_raw or not outcomes_raw:
                continue

            try:
                prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            except Exception:
                continue

            # Find our outcome index
            if outcome in outcomes:
                idx = outcomes.index(outcome)
                cur_p = prices[idx]
                
                # If price reached >= 0.99 or market closed with price near 1
                if cur_p >= 0.99 or (is_closed and cur_p >= 0.90):
                    to_settle.append((trade_id, True, f"Победа исхода '{outcome}' (цена {cur_p}$)"))
                # If price collapsed <= 0.01 or market closed with low price
                elif cur_p <= 0.01 or (is_closed and cur_p < 0.10):
                    to_settle.append((trade_id, False, f"Поражение исхода '{outcome}' (цена {cur_p}$)"))

        for trade_id, won, reason in to_settle:
            settled = self.portfolio.settle_poly_trade(trade_id, won, reason=reason)
            if settled:
                emoji = "🟢" if won else "🔴"
                pnl_str = f"+{settled['pnl']:.2f}$" if settled['pnl'] >= 0 else f"{settled['pnl']:.2f}$"
                msg = (
                    f"{emoji} <b>[POLYMARKET РАСЧЕТ ИСХОДА]</b>\n"
                    f"📌 <b>Рынок:</b> <i>{html.escape(settled['market'])}</i>\n"
                    f"🎯 <b>Исход:</b> <code>{settled['outcome']}</code>\n"
                    f"🚪 <b>Результат:</b> <code>{reason}</code>\n"
                    f"💵 <b>PnL:</b> <code>{pnl_str}</code>\n"
                    f"💰 <b>Новый баланс Poly:</b> <code>${self.portfolio.polymarket_balance:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
                logger.info(f"[Polymarket] Settled {trade_id}: won={won}, pnl={settled['pnl']}")

    def evaluate_market(self, event: Dict[str, Any], market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        question = market.get("question", "")
        slug = market.get("slug", "") or event.get("slug", "")
        prices_raw = market.get("outcomePrices")
        outcomes_raw = market.get("outcomes")
        
        if not prices_raw or not outcomes_raw:
            return None

        try:
            prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except Exception:
            return None

        if len(prices) != len(outcomes) or len(prices) < 2:
            return None

        # Favorite Harvester (Prob > 92% and <= 96.5%)
        for outcome, price in zip(outcomes, prices):
            if 0.92 <= price <= 0.965:
                opp_id = f"fav_{slug}_{outcome}"
                if opp_id not in self.seen_opportunities:
                    return {
                        "strategy": "Favorite Harvester",
                        "market": question,
                        "slug": slug,
                        "reason": f"Фаворит '{outcome}' по цене {round(price, 3)}$ (доходность +{round((1-price)*100, 1)}%)",
                        "action": f"BUY {outcome}",
                        "outcome": outcome,
                        "price": price,
                        "bet_amount": min(config.MAX_BET_PER_POLYMARKET_TRADE, self.portfolio.polymarket_balance)
                    }

        # Spread Harvester
        best_bid = market.get("bestBid")
        best_ask = market.get("bestAsk")
        if best_bid is not None and best_ask is not None:
            try:
                bid = float(best_bid)
                ask = float(best_ask)
                spread = ask - bid
                if 0.03 <= spread <= 0.12 and 0.20 <= bid <= 0.80:
                    opp_id = f"spread_{slug}"
                    if opp_id not in self.seen_opportunities:
                        return {
                            "strategy": "Spread Harvester (Maker)",
                            "market": question,
                            "slug": slug,
                            "reason": f"Широкий спред {round(spread*100, 1)}¢ (Bid: {bid}$, Ask: {ask}$)",
                            "action": "QUOTE SPREAD",
                            "outcome": outcomes[0],
                            "price": round(ask, 3),  # Real ask price!
                            "spread": round(spread, 3),
                            "bet_amount": min(config.MAX_BET_PER_POLYMARKET_TRADE, self.portfolio.polymarket_balance)
                        }
            except (ValueError, TypeError):
                pass

        return None

    async def scan_and_trade(self):
        events = await self.fetch_active_events(limit=35)
        
        # 1. First, settle any completed markets
        await self.check_and_settle_positions(events)

        # 2. Strict limits: max 5 open positions total
        if len(self.portfolio.poly_positions) >= 5:
            return

        # Check existing slugs to avoid cluster risk (max 1 bet per event)
        existing_slugs = {p.get("slug") for p in self.portfolio.poly_positions.values() if p.get("slug")}

        for event in events:
            ev_slug = event.get("slug", "")
            if ev_slug in existing_slugs:
                continue

            markets = event.get("markets", [])
            for market in markets:
                if market.get("closed"):
                    continue

                opp = self.evaluate_market(event, market)
                if not opp:
                    continue

                slug = opp.get("slug", "")
                if slug in existing_slugs:
                    continue

                market_title = opp["market"]
                opp_key = f"{opp['strategy']}_{slug}_{opp.get('outcome', '')}"
                
                if opp_key in self.seen_opportunities:
                    continue

                bet = opp["bet_amount"]
                if bet < 1.0 or self.portfolio.polymarket_balance < bet:
                    continue

                # Execute simulated paper trade
                self.seen_opportunities.add(opp_key)
                price = opp["price"]
                shares = round(bet / price, 2) if price > 0 else 0
                outcome_name = opp.get("outcome", "YES")
                
                trade_id = self.portfolio.record_poly_trade(
                    market_title=market_title,
                    outcome=outcome_name,
                    price=price,
                    amount_usdc=bet,
                    shares=shares,
                    strategy=opp["strategy"],
                    slug=slug
                )

                if trade_id:
                    link = f"https://polymarket.com/event/{slug}"
                    safe_title = html.escape(market_title)
                    safe_reason = html.escape(opp['reason'])
                    safe_strat = html.escape(opp['strategy'])
                    alert = (
                        f"🎯 <b>[POLYMARKET DEMO TRADE]</b>\n"
                        f"📊 <b>Стратегия:</b> <code>{safe_strat}</code>\n"
                        f"📌 <b>Рынок:</b> <i>{safe_title}</i>\n"
                        f"💡 <b>Сигнал:</b> {safe_reason}\n"
                        f"💵 <b>Сумма:</b> <code>${bet:.2f}</code> (Долей: <code>{shares}</code> по <code>${price:.3f}</code>)\n"
                        f"💰 <b>Остаток Poly-баланса:</b> <code>${self.portfolio.polymarket_balance:.2f}</code>\n"
                        f"🔗 <a href='{link}'>Открыть на Polymarket</a>"
                    )
                    await notifier.send_message(alert, parse_mode="HTML")
                    logger.info(f"[Polymarket] Executed paper trade {trade_id}: {market_title}")
                    return
