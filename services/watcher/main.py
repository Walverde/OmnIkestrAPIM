import time
import os
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

WATCH_DIRECTORY = os.getenv("WATCH_DIR", "/data/products")

class ProductFolderHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            logging.info(f"📂 Nova pasta de produto detectada: {event.src_path}")
            # Aqui no futuro faremos a chamada HTTP para o backend avisando sobre o novo produto
        else:
            logging.info(f"📄 Novo arquivo adicionado: {event.src_path}")

    def on_deleted(self, event):
        if event.is_directory:
            logging.info(f"🗑️ Pasta removida: {event.src_path}")

if __name__ == "__main__":
    logging.info(f"🔍 Iniciando File Watcher no diretório: {WATCH_DIRECTORY}")
    
    # Garante que o diretório existe em ambiente local de dev
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