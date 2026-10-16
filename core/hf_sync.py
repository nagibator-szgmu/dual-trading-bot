import json
import logging
import os
import shutil
import threading
import time
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_REPO_ID = "Hhhej/trading-bot-state"

def get_hf_token() -> str:
    env_token = (
        os.getenv("HF_TOKEN")
        or os.getenv("HUGGINGFACE_TOKEN")
        or os.getenv("HF_API_TOKEN")
    )
    if env_token:
        return env_token.strip()

    token_file = os.path.join(os.path.dirname(__file__), "..", ".hf_token")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    return ""



def sync_from_hf(repo_id: str = DEFAULT_REPO_ID, filenames: Optional[List[str]] = None) -> bool:
    """
    Restores state files from Hugging Face Dataset repository on startup.
    Protects 7-day Trade Lock and accumulated PnL from container resets.
    """
    if filenames is None:
        filenames = ["cs2_portfolio_state.json", "portfolio_state.json", "snapshots.csv", "cs2_trades.csv"]

    token = get_hf_token()
    if not token:
        logger.warning("No Hugging Face token found for sync_from_hf.")
        return False

    try:
        from huggingface_hub import HfApi, hf_hub_download
        api = HfApi(token=token)
        remote_files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    except Exception as e:
        logger.warning(f"Could not connect to HF dataset {repo_id}: {e}")
        return False

    success_any = False
    for fname in filenames:
        if fname not in remote_files:
            continue
        try:
            should_restore = False
            # Check local file
            if not os.path.exists(fname) or os.path.getsize(fname) == 0:
                should_restore = True
            elif fname == "cs2_portfolio_state.json":
                # Ensure we never lose active inventory on restart
                try:
                    with open(fname, "r", encoding="utf-8") as f:
                        local_data = json.load(f)
                    if len(local_data.get("inventory", {})) == 0:
                        should_restore = True
                except Exception:
                    should_restore = True

            if should_restore:
                downloaded_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=fname,
                    repo_type="dataset",
                    token=token,
                    force_download=True
                )
                shutil.copy2(downloaded_path, fname)
                logger.info(f"Restored {fname} from HF Dataset {repo_id} successfully.")
                success_any = True
            else:
                logger.info(f"Local {fname} is valid; skipping download.")
        except Exception as e:
            logger.warning(f"Failed to restore {fname} from HF Dataset: {e}")

    return success_any


class HFSyncManager:
    """
    Background worker that uploads state files to Hugging Face Dataset with debouncing.
    Prevents API rate limits and non-blocking operation for trading loops.
    """
    def __init__(self, repo_id: str = DEFAULT_REPO_ID, sync_interval: float = 20.0):
        self.repo_id = repo_id
        self.sync_interval = sync_interval
        self._dirty_files: Set[str] = set()
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="HFSyncWorker")
        self._thread.start()

    def mark_dirty(self, filename: str, immediate: bool = False):
        with self._lock:
            self._dirty_files.add(filename)
        if immediate:
            threading.Thread(target=self._upload_file_direct, args=(filename,), daemon=True).start()

    def _upload_file_direct(self, filename: str):
        if not os.path.exists(filename):
            return
        token = get_hf_token()
        if not token:
            return
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=token)
            api.upload_file(
                path_or_fileobj=filename,
                path_in_repo=filename,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Auto-sync state update: {filename} at {time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            logger.info(f"Successfully synced {filename} to HF Dataset {self.repo_id}")
            with self._lock:
                self._dirty_files.discard(filename)
        except Exception as e:
            logger.warning(f"Error uploading {filename} to HF Dataset: {e}")

    def _worker_loop(self):
        while self._running:
            time.sleep(self.sync_interval)
            files_to_sync = set()
            with self._lock:
                if self._dirty_files:
                    files_to_sync = set(self._dirty_files)
            
            for fname in files_to_sync:
                self._upload_file_direct(fname)


# Global singleton instance
hf_sync_manager = HFSyncManager()

def sync_to_hf(filename: str, immediate: bool = False):
    hf_sync_manager.mark_dirty(filename, immediate=immediate)
