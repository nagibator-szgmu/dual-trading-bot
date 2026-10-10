import aiohttp
import asyncio
import json
import logging
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

    async def fetch_active_events(self, limit: int = 30) -> List[Dict[str, Any]]:
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

        # 1. Dutch Book / Sum of outcomes mispricing
        sum_prices = sum(prices)
        if 0.1 < sum_prices < 0.975:
            profit_potential = round((1.0 - sum_prices) * 100, 2)
            return {
                "strategy": "NegRisk Dutch Book",
                "market": question,
                "slug": slug,
                "reason": f"Сумма исходов = {round(sum_prices, 3)}$ < 1.00$ (Профит +{profit_potential}%)",
                "action": "BUY ALL OUTCOMES",
                "price": round(sum_prices, 3),
                "outcomes": outcomes,
                "prices": prices,
                "bet_amount": min(config.MAX_BET_PER_POLYMARKET_TRADE, self.portfolio.polymarket_balance)
            }

        # 2. Favorite Harvester (High probability > 92% and low variance)
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

        # 3. Spread Harvester
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
                            "price": round((bid + ask) / 2, 3),
                            "spread": round(spread, 3),
                            "bet_amount": min(config.MAX_BET_PER_POLYMARKET_TRADE, self.portfolio.polymarket_balance)
                        }
            except (ValueError, TypeError):
                pass

        return None

    async def scan_and_trade(self):
        events = await self.fetch_active_events(limit=25)
        for event in events:
            markets = event.get("markets", [])
            for market in markets:
                if market.get("closed"):
                    continue

                opp = self.evaluate_market(event, market)
                if not opp:
                    continue

                market_title = opp["market"]
                opp_key = f"{opp['strategy']}_{opp.get('slug', market_title)}"
                
                if opp_key in self.seen_opportunities:
                    continue

                bet = opp["bet_amount"]
                if bet < 1.0 or self.portfolio.polymarket_balance < bet:
                    continue

                # Execute simulated paper trade
                self.seen_opportunities.add(opp_key)
                price = opp["price"]
                shares = round(bet / price, 2) if price > 0 else 0
                outcome_name = opp.get("outcome", "PACK")
                
                trade_id = self.portfolio.record_poly_trade(
                    market_title=market_title,
                    outcome=outcome_name,
                    price=price,
                    amount_usdc=bet,
                    shares=shares,
                    strategy=opp["strategy"]
                )

                if trade_id:
                    link = f"https://polymarket.com/event/{opp.get('slug', '')}"
                    alert = (
                        f"🎯 *[POLYMARKET DEMO TRADE]*\n"
                        f"📊 *Стратегия:* `{opp['strategy']}`\n"
                        f"📌 *Рынок:* _{market_title}_\n"
                        f"💡 *Сигнал:* {opp['reason']}\n"
                        f"💵 *Сумма:* `${bet:.2f}` (Долей: `{shares}` по `${price:.3f}`)\n"
                        f"💰 *Остаток Poly-баланса:* `${self.portfolio.polymarket_balance:.2f}`\n"
                        f"🔗 [Открыть на Polymarket]({link})"
                    )
                    await notifier.send_message(alert)
                    logger.info(f"[Polymarket] Executed paper trade {trade_id}: {market_title}")
                    # Restrict to 1 trade per scan cycle to prevent spamming
                    return
