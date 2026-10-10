import aiohttp
import asyncio
import logging
import os
import html
from config import config

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def auto_discover_chat_id(self) -> bool:
        if self.chat_id:
            return True
        if not self.token:
            return False

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("result", [])
                        for update in reversed(results):
                            msg = update.get("message", {})
                            chat = msg.get("chat", {})
                            cid = chat.get("id")
                            if cid:
                                self.chat_id = str(cid)
                                config.TELEGRAM_CHAT_ID = self.chat_id
                                logger.info(f"Automatically discovered Telegram Chat ID: {self.chat_id}")
                                self._persist_chat_id(self.chat_id)
                                return True
        except Exception as e:
            logger.debug(f"Auto-discover chat_id poll error: {e}")
        return False

    def _persist_chat_id(self, cid: str):
        env_path = ".env"
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        new_lines.append(f"TELEGRAM_CHAT_ID={cid}\n")
                    else:
                        new_lines.append(line)
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
            except Exception:
                pass

    async def send_message(self, text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML") -> bool:
        if not self.token:
            print(f"\n[TG ALERT MOCK]:\n{text}\n")
            return False

        target_id = chat_id or self.chat_id
        if not target_id:
            found = await self.auto_discover_chat_id()
            if not found:
                print(f"\n[TG WAITING FOR /start]:\n{text}\n")
                return False
            target_id = self.chat_id

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": target_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        # Fallback without parse_mode if formatting had an error
                        err = await resp.text()
                        logger.warning(f"Telegram formatting error ({err}), falling back to plain text...")
                        clean_text = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
                        payload_plain = {
                            "chat_id": target_id,
                            "text": clean_text,
                            "disable_web_page_preview": True
                        }
                        async with session.post(url, json=payload_plain) as resp2:
                            return resp2.status == 200
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")
            return False

    async def send_document(self, file_path: str, chat_id: Optional[str] = None, caption: str = "") -> bool:
        target_id = chat_id or self.chat_id
        if not self.token or not target_id:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            data = aiohttp.FormData()
            data.add_field("chat_id", target_id)
            if caption:
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            data.add_field("document", file_bytes, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.warning(f"Failed to send Telegram document: {e}")
            return False

notifier = TelegramNotifier()
