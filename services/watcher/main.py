import logging
import os
import threading
import time
import fnmatch
from pathlib import Path
from typing import Any, Callable

import requests
import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

def _split_watch_dirs() -> list[str]:
    raw_value = os.getenv("WATCH_DIRS") or os.getenv("WATCH_DIR") or "/data/products"
    directories = [
        value.strip()
        for value in raw_value.replace(";", ",").split(",")
        if value.strip()
    ]
    return list(dict.fromkeys(directories or ["/data/products"]))


WATCH_DIRECTORIES = _split_watch_dirs()
WATCH_DIRECTORY = WATCH_DIRECTORIES[0]
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
DEBOUNCE_SECONDS = float(os.getenv("WATCH_DEBOUNCE_SECONDS", "1.5"))
RECONCILE_INTERVAL_SECONDS = int(os.getenv("WATCH_RECONCILE_INTERVAL_SECONDS", "900"))
POLL_INTERVAL_SECONDS = float(os.getenv("WATCH_POLL_INTERVAL_SECONDS", "10.0"))
RESERVED_PRODUCT_DIRECTORY_NAMES = {"images", "videos", "documents"}
DIRECT_MEDIA_EXTENSIONS = {
    "apng", "avi", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "m4a",
    "m4v", "mkv", "mov", "mp3", "mp4", "mpeg", "mpg", "png", "tif",
    "tiff", "wav", "webm", "webp",
}
LEGACY_GROUP_MANIFEST_KEYS = {"schema_version", "id", "title"}
IGNORE_PATTERNS = [
    "*.psd",
    "~$*",
    "*.tmp",
    "Thumbs.db",
    ".DS_Store",
]


def _configured_api_key() -> str:
    secret_file = os.getenv("OMNIKESTRAPIM_API_KEY_FILE", "").strip()
    if secret_file:
        try:
            with open(secret_file, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            logging.exception("Failed to read OMNIKESTRAPIM_API_KEY_FILE")
            return ""
    return os.getenv("OMNIKESTRAPIM_API_KEY", "").strip()


class DebouncedProductNotifier:
    def __init__(
        self,
        watch_directory: str,
        backend_url: str,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        post: Callable[..., Any] | None = None,
    ):
        self.watch_directory = Path(watch_directory).resolve()
        self.backend_url = backend_url.rstrip("/")
        self.debounce_seconds = debounce_seconds
        self.post = post or requests.post
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[dict[str, Any], threading.Timer]] = {}

    def _is_ignored_path(self, path: str) -> bool:
        candidate = Path(path)
        try:
            relative_path = candidate.resolve().relative_to(self.watch_directory)
        except ValueError:
            relative_path = Path(candidate.name)
        if any(part.startswith("_") for part in relative_path.parts):
            return True
        patterns = IGNORE_PATTERNS
        extra_patterns = os.getenv("WATCH_IGNORE_PATTERNS", "")
        if extra_patterns:
            patterns = patterns + [
                pattern.strip()
                for pattern in extra_patterns.replace(";", ",").split(",")
                if pattern.strip()
            ]
        return any(fnmatch.fnmatch(candidate.name, pattern) for pattern in patterns)

    def _is_product_directory(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        try:
            entries = list(path.iterdir())
        except OSError:
            return False
        has_manifest = any(
            entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
            for entry in entries
        )
        has_direct_media = any(
            entry.is_file() and entry.suffix.lower().lstrip(".") in DIRECT_MEDIA_EXTENSIONS
            for entry in entries
        )
        manifest_path = next(
            (
                entry
                for entry in entries
                if entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
            ),
            None,
        )
        has_children = any(entry.is_dir() for entry in entries)
        if (
            has_manifest
            and has_children
            and not has_direct_media
            and manifest_path is not None
            and self._is_legacy_group_manifest(manifest_path)
        ):
            return False
        return has_manifest or has_direct_media

    @staticmethod
    def _is_legacy_group_manifest(manifest_path: Path) -> bool:
        try:
            metadata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return False
        return isinstance(metadata, dict) and set(metadata) == LEGACY_GROUP_MANIFEST_KEYS

    def _product_key(self, path: str, is_directory: bool = False) -> str:
        candidate = Path(path).resolve()
        try:
            relative_path = candidate.relative_to(self.watch_directory)
        except ValueError:
            return str(candidate)
        if not relative_path.parts:
            return str(self.watch_directory)

        ancestor = candidate if is_directory or candidate.is_dir() else candidate.parent
        while ancestor != self.watch_directory and self.watch_directory in ancestor.parents:
            if (
                ancestor.name.casefold() not in RESERVED_PRODUCT_DIRECTORY_NAMES
                and self._is_product_directory(ancestor)
            ):
                return str(ancestor)
            ancestor = ancestor.parent
        return str(self.watch_directory / relative_path.parts[0])

    def submit(
        self,
        event_type: str,
        path: str,
        is_directory: bool,
        destination_path: str | None = None,
    ) -> None:
        if self._is_ignored_path(destination_path or path):
            return
        payload = {
            "event": event_type,
            "path": path,
            "destination_path": destination_path,
            "is_directory": is_directory,
            "service": "watcher",
        }
        key = self._product_key(destination_path or path, is_directory)
        with self._lock:
            previous = self._pending.get(key)
            if previous and previous[0]["event"] == "moved" and event_type != "moved":
                payload = previous[0]
            if previous:
                previous[1].cancel()
            timer = threading.Timer(self.debounce_seconds, self.flush, args=(key,))
            timer.daemon = True
            self._pending[key] = (payload, timer)
            timer.start()

    def _send(self, payload: dict[str, Any]) -> None:
        try:
            api_key = _configured_api_key()
            headers = {"X-API-Key": api_key} if api_key else {}
            response = self.post(
                f"{self.backend_url}/api/products/watch",
                json=payload,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            logging.info("Watcher event sent: %s %s", payload["event"], payload["path"])
        except requests.RequestException:
            logging.exception("Failed to notify backend for %s", payload["path"])

    def flush(self, key: str) -> None:
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending:
            self._send(pending[0])

    def flush_all(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            for _, timer in pending:
                timer.cancel()
        for payload, _ in pending:
            self._send(payload)


class ProductFolderHandler(FileSystemEventHandler):
    def __init__(self, notifier: DebouncedProductNotifier):
        self.notifier = notifier

    def _queue_event(
        self,
        event_type: str,
        path: str,
        is_directory: bool,
        destination_path: str | None = None,
    ) -> None:
        self.notifier.submit(event_type, path, is_directory, destination_path)

    def on_created(self, event):
        self._queue_event("created", event.src_path, event.is_directory)

    def on_modified(self, event):
        self._queue_event("modified", event.src_path, event.is_directory)

    def on_deleted(self, event):
        self._queue_event("deleted", event.src_path, event.is_directory)

    def on_moved(self, event):
        self._queue_event(
            "moved", event.src_path, event.is_directory, event.dest_path
        )


def reconcile_catalog(backend_url: str = BACKEND_URL) -> None:
    try:
        api_key = _configured_api_key()
        headers = {"X-API-Key": api_key} if api_key else {}
        response = requests.post(
            f"{backend_url}/api/products/scan",
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        logging.info("Periodic full catalog reconciliation completed")
    except requests.RequestException:
        logging.exception("Periodic full catalog reconciliation failed")


if __name__ == "__main__":
    logging.info("🔍 Iniciando File Watcher nos diretórios: %s", WATCH_DIRECTORIES)
    notifiers = []
    observer = PollingObserver(timeout=POLL_INTERVAL_SECONDS)
    for watch_directory in WATCH_DIRECTORIES:
        os.makedirs(watch_directory, exist_ok=True)
        notifier = DebouncedProductNotifier(watch_directory, BACKEND_URL)
        notifiers.append(notifier)
        event_handler = ProductFolderHandler(notifier)
        observer.schedule(event_handler, path=watch_directory, recursive=True)
    observer.start()
    next_reconciliation = time.monotonic() + RECONCILE_INTERVAL_SECONDS

    try:
        while True:
            time.sleep(1)
            heartbeat = Path("/tmp/watcher-heartbeat")
            try:
                heartbeat.touch()
            except OSError:
                pass
            if time.monotonic() >= next_reconciliation:
                reconcile_catalog()
                next_reconciliation = time.monotonic() + RECONCILE_INTERVAL_SECONDS
    except KeyboardInterrupt:
        observer.stop()
        for notifier in notifiers:
            notifier.flush_all()
        logging.info("File watcher stopped")

    observer.join()