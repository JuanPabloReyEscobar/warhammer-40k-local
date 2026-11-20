from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("WH40K_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "warhammer40k.sqlite3"
SOURCE_URL = "https://wahapedia.ru/wh40k11ed/the-rules/data-export/"

