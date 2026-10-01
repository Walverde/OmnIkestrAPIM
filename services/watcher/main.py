import logging
import os
import time

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

WATCH_DIRECTORY = os.getenv("WATCH_DIR", "/data/products")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")


class ProductFolderHandler(FileSystemEventHandler):
    def _notify_backend(self, event_type: str, src_path: str, is_directory: bool):
        payload = {
            "event": event_type,
            "path": src_path,
            "is_directory": is_directory,
            "service": "watcher",
        }

        try:
            response = requests.post(
                f"{BACKEND_URL}/api/products/watch",
                json=payload,
                timeout=5,
            )
            response.raise_for_status()
            logging.info(
                "📡 Evento enviado para o backend: %s %s - status=%s",
                event_type,
                src_path,
                response.status_code,
            )
        except requests.RequestException as exc:
            logging.exception(
                "⚠️ Falha ao notificar backend sobre %s em %s: %s",
                event_type,
                src_path,
                exc,
            )

    def on_created(self, event):
        if event.is_directory:
            logging.info("📂 Nova pasta de produto detectada: %s", event.src_path)
        else:
            logging.info("📄 Novo arquivo adicionado: %s", event.src_path)
        self._notify_backend("created", event.src_path, event.is_directory)

    def on_deleted(self, event):
        if event.is_directory:
            logging.info("🗑️ Pasta removida: %s", event.src_path)
        else:
            logging.info("🗑️ Arquivo removido: %s", event.src_path)
        self._notify_backend("deleted", event.src_path, event.is_directory)

    def on_moved(self, event):
        logging.info("🔁 Item movido: %s -> %s", event.src_path, event.dest_path)
        self._notify_backend("moved", event.dest_path, event.is_directory)


if __name__ == "__main__":
    logging.info("🔍 Iniciando File Watcher no diretório: %s", WATCH_DIRECTORY)
    if not os.path.exists(WATCH_DIRECTORY):
        os.makedirs(WATCH_DIRECTORY, exist_ok=True)

    event_handler = ProductFolderHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIRECTORY, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("🛑 File Watcher interrompido.")

    observer.join()