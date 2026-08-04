"""每日备份 data/diet.db + data/local_nutrition.json 到 iCloud Drive。

按日期分目录（YYYY-MM-DD），保留最近 BACKUP_KEEP_DAYS 天，更早的自动清理。
不备份 data/chroma/（语义搜索索引，可随时用 build_recipe_embeddings.py 重建，不是数据源）。

DB 用 sqlite3 在线备份 API 而不是文件拷贝：数据库是 WAL 模式，且备份跑的时候
Streamlit 常驻进程正开着连接，已提交的事务可能还在 diet.db-wal 里没合并进主文件。
直接 copy 只会拿到主文件，得到的副本可能缺数据甚至不一致——而且是静默的，
等到真要恢复才会发现。backup API 会自己处理加锁和 WAL，产出的是一致快照。

用法：python3.9 scripts/backup_to_icloud.py
配合 launchd（见 com.dietmanager.backup.plist）每天自动跑一次。
"""
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, DB_PATH, ICLOUD_BACKUP_PATH

BACKUP_KEEP_DAYS = 14
PLAIN_FILES_TO_BACKUP = [DATA_DIR / "local_nutrition.json"]


def _backup_sqlite(src: Path, dest: Path) -> None:
    """Consistent snapshot of a live WAL-mode DB (see module docstring)."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def backup() -> None:
    today_dir = ICLOUD_BACKUP_PATH / datetime.now().strftime("%Y-%m-%d")
    today_dir.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        _backup_sqlite(DB_PATH, today_dir / DB_PATH.name)

    for src in PLAIN_FILES_TO_BACKUP:
        if src.exists():
            shutil.copy2(src, today_dir / src.name)

    _prune_old_backups()


def _prune_old_backups() -> None:
    if not ICLOUD_BACKUP_PATH.exists():
        return
    cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
    for entry in ICLOUD_BACKUP_PATH.iterdir():
        if not entry.is_dir():
            continue
        try:
            entry_date = datetime.strptime(entry.name, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date < cutoff:
            shutil.rmtree(entry)


if __name__ == "__main__":
    backup()
    print(f"备份完成：{ICLOUD_BACKUP_PATH}")
