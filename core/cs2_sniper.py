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

    def fetch_skinport_catalog(self) -> List[Dict[str, Any]]:
        url = "https://api.skinport.com/v1/items?app_id=730&currency=USD"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Encoding": "br"
        })
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw_compressed = resp.read()
                try:
                    import brotli
                    decompressed = brotli.decompress(raw_compressed)
                except Exception:
                    # Fallback to system brotli CLI
                    p = subprocess.Popen(["brotli", "-d"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    decompressed, _ = p.communicate(input=raw_compressed)
                
                return json.loads(decompressed)
        except Exception as e:
            logger.warning(f"Error fetching Skinport catalog: {e}")
            return []

    def get_top_discounts(self, limit: int = 5) -> List[Dict[str, Any]]:
        items = self.fetch_skinport_catalog()
        candidates = []
        for item in items:
            min_p = item.get("min_price")
            sug_p = item.get("suggested_price")
            qty = item.get("quantity", 0)
            if min_p and sug_p and min_p >= config.CS2_MIN_ITEM_PRICE and qty >= 2:
                disc = (sug_p - min_p) / sug_p * 100
                net_profit = (sug_p * 0.95) - min_p
                net_pct = (net_profit / min_p) * 100
                if disc >= config.CS2_MIN_DISCOUNT_PCT and net_pct >= config.CS2_MIN_PROFIT_PCT:
                    candidates.append({
                        "name": item["market_hash_name"],
                        "min_price": min_p,
                        "suggested_price": sug_p,
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

        # 1. Check Trade Lock expirations on held inventory (Simulate Auto-Sell at Fair Price)
        for trade_id, item in list(self.inventory.items()):
            lock_until = item.get("trade_lock_until", now_ts)
            if now_ts >= lock_until:
                # 7 days elapsed -> item is unlocked and sold at fair suggested price
                gross_sale = item["fair_price"]
                net_sale = round(gross_sale * 0.95, 2)  # 5% total sell/cashout fee
                profit = round(net_sale - item["buy_price"], 2)
                profit_pct = round((profit / item["buy_price"]) * 100, 2)
                
                self.cash += net_sale
                sale_record = {
                    "action": "SELL",
                    "trade_id": trade_id,
                    "name": item["name"],
                    "buy_price": item["buy_price"],
                    "sell_price": gross_sale,
                    "net_sale": net_sale,
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "reason": "Trade Lock завершен: продажа по Fair Price",
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

        # 3. Fetch live catalog and find best discount candidates
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
            lock_until = now_ts + (7 * 86400)  # 7 days trade lock

            snipe_entry = {
                "id": trade_id,
                "name": name,
                "buy_price": buy_price,
                "fair_price": cand["suggested_price"],
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
                "fair_price": cand["suggested_price"],
                "discount_pct": cand["discount_pct"],
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            new_snipes.append(snipe_entry)

        self.save()
        return {"sniped": new_snipes, "sold": sold_items}

    def get_summary(self) -> Dict[str, Any]:
        now_ts = time.time()
        invested = sum(item["buy_price"] for item in self.inventory.values())
        fair_val = sum(item["fair_price"] * 0.95 for item in self.inventory.values())
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
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "Action", "Item_Name", "Buy_Price",
                "Fair_or_Sell_Price", "Discount_Percent", "Profit_USD", "Profit_Percent", "Reason"
            ])
            for h in self.history:
                writer.writerow([
                    h.get("time", ""),
                    h.get("action", ""),
                    h.get("name", ""),
                    h.get("buy_price", ""),
                    h.get("sell_price", h.get("fair_price", "")),
                    f"{h.get('discount_pct', '')}%" if h.get("discount_pct") else "",
                    h.get("profit", ""),
                    f"{h.get('profit_pct', '')}%" if h.get("profit_pct") else "",
                    h.get("reason", "")
                ])
        return filename

cs2_sniper = CS2SkinSniper(initial_budget=config.CS2_BUDGET)
