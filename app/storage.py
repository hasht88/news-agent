import json, os
from pathlib import Path
from typing import List
from app.models import AgentSettings

# DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "settings.json"

if os.environ.get("VERCEL"):
    DATA_DIR = Path("/tmp/data")
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DATA_FILE = DATA_DIR / "settings.json"

DEFAULT_URLS: List[str] = []

DEFAULT_KEYWORDS: List[str] = []

def ensure_data_dir():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

def get_default_settings() -> AgentSettings:
    return AgentSettings(
        sources=list(DEFAULT_URLS),
        keywords=list(DEFAULT_KEYWORDS)
    )

def load_settings() -> AgentSettings:
    ensure_data_dir()
    if not DATA_FILE.exists():
        default_settings = get_default_settings()
        save_settings(default_settings)
        return default_settings
    
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return AgentSettings(
                sources=data.get("sources", []),
                keywords=data.get("keywords", [])
            )
    except Exception as e:
        print(f"Error loading settings, using defaults: {e}")
        return get_default_settings()

def save_settings(settings: AgentSettings) -> bool:
    ensure_data_dir()
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(settings.model_dump(), f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving settings: {e}")
        return False

