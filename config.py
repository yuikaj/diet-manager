import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "diet.db"
CHROMA_PATH = DATA_DIR / "chroma"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
USDA_API_KEY = os.getenv("USDA_API_KEY")

# Nutrition advisor: max calls per day (Gemini free tier protection)
AI_ADVICE_DAILY_LIMIT = 3

# Cooking constraints
MAX_WOK_DISHES = 1

# Serving size
SERVING_PEOPLE = 2
MIN_VEGGIE_WEIGHT_G = 700   # per dinner for 2 people
MIN_PROTEIN_WEIGHT_G = 400  # per dinner for 2 people

# Protein target multiplier range (g per kg body weight per day)
PROTEIN_MULTIPLIER_MIN = 1.2
PROTEIN_MULTIPLIER_MAX = 1.5

# iCloud backup path (macOS default)
ICLOUD_BACKUP_PATH = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/diet_backup"
