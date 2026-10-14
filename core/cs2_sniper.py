import csv
import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Any, Optional
import urllib.request
from config import config

logger = logging.getLogger(__name__)
CS2_STATE_FILE = "cs2_portfolio_state.json"

class CS2SkinSniper:
    def __init__(self, initial_budget: float = 200.0):
        self.initial = initial_budget
        self.cash = initial_budget
        self.inventory: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.max_inventory_items = 6

        # In-memory caching to avoid Skinport HTTP 429 rate limit
        self._catalog_cache: List[Dict[str, Any]] = []
        self._last_catalog_ts: float = 0.0
        self._history_cache: Dict[str, Dict[str, Any]] = {}
        self._last_history_ts: float = 0.0

        self.load()

    def load(self):
        if os.path.exists(CS2_STATE_FILE):
            try:
                with open(CS2_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.cash = data.get("cash", self.cash)
                    self.initial = data.get("initial", self.initial)
                    self.inventory = data.get("inventory", {})
                    self.history = data.get("history", [])
            except Exception as e:
                logger.warning(f"Error loading CS2 state: {e}")

    def save(self):
        temp_file = f"{CS2_STATE_FILE}.tmp"
        data = {
            "initial": self.initial,
            "cash": round(self.cash, 2),
            "inventory": self.inventory,
            "history": self.history[-500:]
        }
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, CS2_STATE_FILE)
        except Exception:
            with open(CS2_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def _decompress_brotli(self, raw_bytes: bytes) -> bytes:
        try:
            import brotli
            return brotli.decompress(raw_bytes)
        except Exception:
            p = subprocess.Popen(["brotli", "-d"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = p.communicate(input=raw_bytes)
            return out

    def fetch_skinport_catalog(self, force: bool = False) -> List[Dict[str, Any]]:
        now = time.time()
        if not force and self._catalog_cache and (now - self._last_catalog_ts < 120):
            return self._catalog_cache

        url = "https://api.skinport.com/v1/items?app_id=730&currency=USD"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Encoding": "br"
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                decompressed = self._decompress_brotli(resp.read())
                items = json.loads(decompressed)
                self._catalog_cache = items
                self._last_catalog_ts = now
                return items
        except Exception as e:
            logger.warning(f"Error fetching Skinport catalog: {e}")
            return self._catalog_cache

    def fetch_skinport_sales_history(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if not force and self._history_cache and (now - self._last_history_ts < 600):
            return self._history_cache

        url = "https://api.skinport.com/v1/sales/history?app_id=730&currency=USD"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Encoding": "br"
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                decompressed = self._decompress_brotli(resp.read())
                records = json.loads(decompressed)
                history_map = {r["market_hash_name"]: r for r in records if "market_hash_name" in r}
                self._history_cache = history_map
                self._last_history_ts = now
                return history_map
        except Exception as e:
            logger.warning(f"Error fetching Skinport sales history: {e}")
            return self._history_cache

    def get_top_discounts(self, limit: int = 5) -> List[Dict[str, Any]]:
        items = self.fetch_skinport_catalog()
        history_map = self.fetch_skinport_sales_history()
        candidates = []

        for item in items:
            name = item.get("market_hash_name")
            min_p = item.get("min_price")
            sug_p = item.get("suggested_price")
            qty = item.get("quantity", 0)

            if not (name and min_p and sug_p and min_p >= config.CS2_MIN_ITEM_PRICE and min_p <= config.CS2_MAX_ITEM_PRICE and qty >= 2):
                continue

            hist = history_map.get(name)
            if not hist:
                continue

            last_30 = hist.get("last_30_days") or {}
            vol_30 = last_30.get("volume") or 0
            avg_30 = last_30.get("avg")

            last_7 = hist.get("last_7_days") or {}
            vol_7 = last_7.get("volume") or 0

            # Mathematical liquidity & velocity filter:
            # 1. Minimum 30d volume
            # 2. Turnover velocity: average interval between sales <= 48h
            # 3. Active recent trading: at least 3 sales in last 7 days
            hours_between_sales = (30 * 24) / vol_30 if vol_30 > 0 else 999.0
            if (vol_30 < config.CS2_MIN_SALES_LAST_30D or 
                hours_between_sales > config.CS2_MAX_AVG_HOURS_BETWEEN_SALES or 
                vol_7 < config.CS2_MIN_SALES_LAST_7D or 
                not avg_30 or avg_30 <= 0):
                continue

            # Conservative valuation: minimum between actual 30d transaction avg and algorithmic suggested price
            fair_p = min(avg_30, sug_p)
            if min_p >= fair_p:
                continue

            disc = (fair_p - min_p) / fair_p * 100
            net_sale = fair_p * (1.0 - config.CS2_FEE_PCT)
            net_profit = net_sale - min_p
            net_pct = (net_profit / min_p) * 100

            if disc >= config.CS2_MIN_DISCOUNT_PCT and net_pct >= config.CS2_MIN_PROFIT_PCT:
                candidates.append({
                    "name": name,
                    "min_price": min_p,
                    "suggested_price": sug_p,
                    "avg_30": avg_30,
                    "vol_30": vol_30,
                    "fair_price": round(fair_p, 2),
                    "discount_pct": round(disc, 1),
                    "net_profit": round(net_profit, 2),
                    "net_pct": round(net_pct, 1),
                    "quantity": qty,
                    "url": item.get("item_page", "")
                })

        candidates.sort(key=lambda x: x["discount_pct"], reverse=True)
        return candidates[:limit]

    def scan_and_snipe(self) -> Dict[str, List[Dict[str, Any]]]:
        now_ts = time.time()
        new_snipes = []
        sold_items = []

        # 1. Check Trade Lock expirations on held inventory (Dynamic Market Price Sale)
        expired_ids = [tid for tid, it in self.inventory.items() if now_ts >= it.get("trade_lock_until", now_ts)]
        if expired_ids:
            fresh_hist = self.fetch_skinport_sales_history()
            fresh_cat = {c.get("market_hash_name"): c for c in self.fetch_skinport_catalog()}

            for trade_id in expired_ids:
                item = self.inventory[trade_id]
                name = item["name"]
                old_fair = item.get("fair_price", item["buy_price"])

                # Fresh price resolution from Skinport API
                h = fresh_hist.get(name, {})
                c = fresh_cat.get(name, {})
                avg_30 = h.get("last_30_days", {}).get("avg")
                sug_p = c.get("suggested_price") or h.get("suggested_price")

                if avg_30 and sug_p:
                    fresh_market_price = min(avg_30, sug_p)
                elif avg_30:
                    fresh_market_price = avg_30
                elif sug_p:
                    fresh_market_price = sug_p
                else:
                    fresh_market_price = old_fair

                # Conservative liquidity execution: 1.5% discount off fair market price to guarantee instant fill
                gross_sale = round(fresh_market_price * 0.985, 2)
                net_sale = round(gross_sale * (1.0 - config.CS2_FEE_PCT), 2)
                profit = round(net_sale - item["buy_price"], 2)
                profit_pct = round((profit / item["buy_price"]) * 100, 2)
                drift_pct = round((fresh_market_price - old_fair) / old_fair * 100, 1) if old_fair > 0 else 0.0

                self.cash = round(self.cash + net_sale, 2)
                sale_record = {
                    "action": "SELL",
                    "trade_id": trade_id,
                    "name": name,
                    "buy_price": item["buy_price"],
                    "sell_price": gross_sale,
                    "net_sale": net_sale,
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "initial_fair": old_fair,
                    "actual_market": round(fresh_market_price, 2),
                    "price_drift_pct": drift_pct,
                    "reason": f"Trade Lock снят: продажа по рынку (дрифт {drift_pct:+.1f}%)",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                self.history.append(sale_record)
                sold_items.append(sale_record)
                del self.inventory[trade_id]

        # 2. Check if we have capacity and cash to snipe new items
        current_inv_count = len(self.inventory)
        if current_inv_count >= self.max_inventory_items or self.cash < config.CS2_MIN_ITEM_PRICE:
            self.save()
            return {"sniped": new_snipes, "sold": sold_items}

        # 3. Fetch live catalog + sales history to find best verified discount candidates
        candidates = self.get_top_discounts(limit=25)
        existing_names = {item["name"] for item in self.inventory.values()}

        for cand in candidates:
            if len(self.inventory) >= self.max_inventory_items:
                break

            name = cand["name"]
            buy_price = cand["min_price"]

            if name in existing_names:
                continue
            if buy_price > config.CS2_MAX_ITEM_PRICE or buy_price > self.cash:
                continue

            # Execute Paper Buy
            self.cash -= buy_price
            trade_id = f"cs2_{abs(hash(name)) % 1000000}_{int(now_ts)}"
            lock_until = now_ts + (7 * 86400)

            snipe_entry = {
                "id": trade_id,
                "name": name,
                "buy_price": buy_price,
                "fair_price": cand["fair_price"],
                "suggested_price": cand["suggested_price"],
                "avg_30": cand["avg_30"],
                "vol_30": cand["vol_30"],
                "discount_pct": cand["discount_pct"],
                "est_profit": cand["net_profit"],
                "est_profit_pct": cand["net_pct"],
                "quantity_market": cand["quantity"],
                "url": cand["url"],
                "bought_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "bought_at_ts": now_ts,
                "trade_lock_until": lock_until
            }
            self.inventory[trade_id] = snipe_entry
            existing_names.add(name)

            self.history.append({
                "action": "BUY",
                "trade_id": trade_id,
                "name": name,
                "buy_price": buy_price,
                "fair_price": cand["fair_price"],
                "discount_pct": cand["discount_pct"],
                "vol_30": cand["vol_30"],
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            new_snipes.append(snipe_entry)

        self.save()
        return {"sniped": new_snipes, "sold": sold_items}

    def get_summary(self) -> Dict[str, Any]:
        now_ts = time.time()
        invested = sum(item["buy_price"] for item in self.inventory.values())
        fair_val = sum(item["fair_price"] * (1.0 - config.CS2_FEE_PCT) for item in self.inventory.values())
        equity = round(self.cash + fair_val, 2)
        total_pnl = round(equity - self.initial, 2)
        total_pnl_pct = round((total_pnl / self.initial) * 100, 2)

        sales = [h for h in self.history if h.get("action") == "SELL"]
        wins = sum(1 for s in sales if s.get("profit", 0) > 0)
        win_rate = round((wins / len(sales) * 100), 1) if sales else 0.0

        items_list = []
        for tid, item in self.inventory.items():
            days_left = max(0, int((item["trade_lock_until"] - now_ts) / 86400) + 1)
            items_list.append({
                "id": tid,
                "name": item["name"],
                "buy": item["buy_price"],
                "fair": item["fair_price"],
                "disc": item["discount_pct"],
                "vol_30": item.get("vol_30", 0),
                "est_profit": item["est_profit"],
                "days_left": days_left,
                "url": item.get("url", "")
            })

        return {
            "initial": self.initial,
            "equity": equity,
            "cash": round(self.cash, 2),
            "invested": round(invested, 2),
            "unrealized_profit": round(fair_val - invested, 2),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "inventory_count": len(self.inventory),
            "sold_count": len(sales),
            "win_rate": win_rate,
            "items": items_list
        }

    def export_csv(self, filename: str = "cs2_trades.csv") -> str:
        now_ts = time.time()
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "Action", "Item_Name", "Buy_Price",
                "Fair_or_Sell_Price", "Initial_Fair_Price", "Price_Drift_Percent", "Discount_Percent", "Vol_30d", "Profit_USD", "Profit_Percent", "Reason"
            ])
            # 1. Closed and executed history
            for h in self.history:
                writer.writerow([
                    h.get("time", ""),
                    h.get("action", ""),
                    h.get("name", ""),
                    h.get("buy_price", ""),
                    h.get("sell_price", h.get("fair_price", "")),
                    h.get("initial_fair", h.get("fair_price", "")),
                    f"{h.get('price_drift_pct', 0.0):+.1f}%" if "price_drift_pct" in h else "",
                    f"{h.get('discount_pct', '')}%" if h.get("discount_pct") else "",
                    h.get("vol_30", ""),
                    h.get("profit", ""),
                    f"{h.get('profit_pct', '')}%" if h.get("profit_pct") else "",
                    h.get("reason", "")
                ])

            # 2. Currently held inventory (locked or awaiting unlock)
            for tid, item in self.inventory.items():
                days_left = max(0, int((item.get("trade_lock_until", now_ts) - now_ts) / 86400) + 1)
                net_sale = item["fair_price"] * (1.0 - config.CS2_FEE_PCT)
                unrealized_profit = round(net_sale - item["buy_price"], 2)
                unrealized_pct = round((unrealized_profit / item["buy_price"]) * 100, 1)
                writer.writerow([
                    item.get("bought_at", ""),
                    "HOLD_TRADE_LOCK",
                    item.get("name", ""),
                    item.get("buy_price", ""),
                    item.get("fair_price", ""),
                    item.get("fair_price", ""),
                    "+0.0%",
                    f"{item.get('discount_pct', '')}%",
                    item.get("vol_30", ""),
                    f"+${unrealized_profit:.2f}" if unrealized_profit >= 0 else f"-${abs(unrealized_profit):.2f}",
                    f"{unrealized_pct:+}%",
                    f"Trade Lock активен (осталось ~{days_left} дн.)"
                ])
        return filename

cs2_sniper = CS2SkinSniper(initial_budget=config.CS2_BUDGET)
