import logging
import os
import fnmatch
import threading
import time
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

def _split_env_paths(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [part.strip() for part in raw_value.replace(";", ",").split(",") if part.strip()]


def _resolve_watch_directories() -> tuple[Path, ...]:
    configured_roots = _split_env_paths(os.getenv("WATCH_DIRS") or os.getenv("WATCH_DIR"))
    if not configured_roots:
        configured_roots = _split_env_paths(os.getenv("PRODUCT_ROOTS")) or ["/data/products"]
    if not configured_roots:
        configured_roots = ["/data/products"]

    resolved_roots: list[Path] = []
    seen: set[str] = set()
    for root in configured_roots:
        normalized = Path(root).expanduser().resolve()
        key = str(normalized)
        if key not in seen:
            seen.add(key)
            resolved_roots.append(normalized)
    return tuple(resolved_roots)


WATCH_DIRECTORIES = _resolve_watch_directories()
WATCH_DIRECTORY = str(WATCH_DIRECTORIES[0]) if WATCH_DIRECTORIES else "/data/products"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
DEBOUNCE_SECONDS = float(os.getenv("WATCH_DEBOUNCE_SECONDS", "1.5"))
RECONCILE_INTERVAL_SECONDS = int(os.getenv("WATCH_RECONCILE_INTERVAL_SECONDS", "900"))
POLL_INTERVAL_SECONDS = float(os.getenv("WATCH_POLL_INTERVAL_SECONDS", "1.0"))
RESERVED_PRODUCT_DIRECTORY_NAMES = {"images", "videos", "documents"}
DIRECT_MEDIA_EXTENSIONS = {
    "apng", "avi", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "m4a",
    "m4v", "mkv", "mov", "mp3", "mp4", "mpeg", "mpg", "png", "tif",
    "tiff", "wav", "webm", "webp",
}
LEGACY_GROUP_MANIFEST_KEYS = {"schema_version", "id", "title"}
DEFAULT_IGNORE_PATTERNS = ("*.psd", "~$*", "*.tmp", "Thumbs.db", ".DS_Store")
IGNORE_PATTERNS = tuple(
    pattern.strip()
    for pattern in (
        os.getenv("WATCH_IGNORE_PATTERNS") or ",".join(DEFAULT_IGNORE_PATTERNS)
    ).split(",")
    if pattern.strip()
)


class DebouncedProductNotifier:
    def __init__(
        self,
        watch_directory: str | tuple[str, ...] | None,
        backend_url: str,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        post: Callable[..., Any] | None = None,
    ):
        if isinstance(watch_directory, (tuple, list)):
            self.watch_directories = tuple(Path(path).expanduser().resolve() for path in watch_directory)
        else:
            self.watch_directories = (Path(watch_directory or WATCH_DIRECTORY).expanduser().resolve(),)
        self.watch_directory = self.watch_directories[0]
        self.backend_url = backend_url.rstrip("/")
        self.debounce_seconds = debounce_seconds
        self.post = post or requests.post
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[dict[str, Any], threading.Timer]] = {}

    def _root_for_path(self, path: Path) -> Path | None:
        for root in self.watch_directories:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return None

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
        root = self._root_for_path(candidate)
        if root is None:
            return str(candidate)
        try:
            relative_path = candidate.relative_to(root)
        except ValueError:
            return str(candidate)
        if not relative_path.parts:
            return str(root)

        ancestor = candidate if is_directory or candidate.is_dir() else candidate.parent
        while ancestor != root and root in ancestor.parents:
            if (
                ancestor.name.casefold() not in RESERVED_PRODUCT_DIRECTORY_NAMES
                and self._is_product_directory(ancestor)
            ):
                return str(ancestor)
            ancestor = ancestor.parent
        return str(root / relative_path.parts[0])

    def submit(
        self,
        event_type: str,
        path: str,
        is_directory: bool,
        destination_path: str | None = None,
    ) -> None:
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
            response = self.post(
                f"{self.backend_url}/api/products/watch",
                json=payload,
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
    def __init__(
        self,
        notifier: DebouncedProductNotifier,
        ignore_patterns: tuple[str, ...] = IGNORE_PATTERNS,
    ):
        self.notifier = notifier
        self.ignore_patterns = ignore_patterns

    def _is_ignored(self, path: str) -> bool:
        candidate = Path(path).resolve()
        root = self.notifier._root_for_path(candidate)
        if root is None:
            return True
        relative_path = candidate.relative_to(root)
        path_parts = tuple(part for part in relative_path.parts if part)

        if any(part.startswith("_") for part in path_parts):
            return True
        return any(
            fnmatch.fnmatchcase(relative_path.as_posix(), pattern)
            or any(fnmatch.fnmatchcase(part, pattern) for part in path_parts)
            for pattern in self.ignore_patterns
        )

    def _queue_event(
        self,
        event_type: str,
        path: str,
        is_directory: bool,
        destination_path: str | None = None,
    ) -> None:
        if event_type == "moved" and destination_path:
            source_ignored = self._is_ignored(path)
            destination_ignored = self._is_ignored(destination_path)
            if source_ignored and destination_ignored:
                return
            if source_ignored:
                event_type, path, destination_path = "created", destination_path, None
            elif destination_ignored:
                event_type, destination_path = "deleted", None
        elif self._is_ignored(path):
            return
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
        response = requests.post(f"{backend_url}/api/products/scan", timeout=120)
        response.raise_for_status()
        logging.info("Periodic full catalog reconciliation completed")
    except requests.RequestException:
        logging.exception("Periodic full catalog reconciliation failed")


if __name__ == "__main__":
    logging.info("🔍 Iniciando File Watcher nos diretórios: %s", ", ".join(str(path) for path in WATCH_DIRECTORIES))
    for watch_directory in WATCH_DIRECTORIES:
        if not os.path.exists(watch_directory):
            os.makedirs(watch_directory, exist_ok=True)

    notifier = DebouncedProductNotifier(WATCH_DIRECTORIES, BACKEND_URL)
    event_handler = ProductFolderHandler(notifier)
    observer = PollingObserver(timeout=POLL_INTERVAL_SECONDS)
    for watch_directory in WATCH_DIRECTORIES:
        observer.schedule(event_handler, path=str(watch_directory), recursive=True)
    observer.start()
    next_reconciliation = time.monotonic() + RECONCILE_INTERVAL_SECONDS

    try:
        while True:
            time.sleep(1)
            if time.monotonic() >= next_reconciliation:
                reconcile_catalog()
                next_reconciliation = time.monotonic() + RECONCILE_INTERVAL_SECONDS
    except KeyboardInterrupt:
        observer.stop()
        notifier.flush_all()
        logging.info("File watcher stopped")

    observer.join()