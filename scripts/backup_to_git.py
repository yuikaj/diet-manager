"""每日把个人数据备份到私有 GitHub 仓库（diet-manager-data）。

存的是**文本 SQL dump 而不是二进制 .db**：git 对二进制几乎无法做增量压缩，
每天提交一个 1MB 的 .db 一年会让仓库涨到 130MB+；而文本 dump 每天只增加
实际变化的那几 KB，一年约 5-10MB。副作用是能在 GitHub 网页上直接看 diff
（"上周三我改了哪道菜"一目了然）。

dump 前先用 sqlite3 在线备份 API 取一致性快照——数据库是 WAL 模式且
Streamlit 常驻进程开着连接，直接 dump 活动库可能读到撕裂的中间状态。

恢复方法见数据仓库里的 README。

用法：python3.9 scripts/backup_to_git.py
由 launchd（com.dietmanager.gitbackup.plist）每天自动触发。
"""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, DB_PATH

REPO_DIR = Path.home() / "Documents" / "Projects" / "diet-manager-data"
REMOTE = "git@github.com:yuikaj/diet-manager-data.git"


def _git(*args, check=True):
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, check=check,
        capture_output=True, text=True,
    )


def _ensure_repo() -> None:
    if (REPO_DIR / ".git").exists():
        return
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    _git("init", "-q")
    # `git init -b main` needs git >= 2.28; this box has 2.23, so set the branch
    # name explicitly instead (works on any version, and on an empty repo it is
    # just a HEAD rewrite).
    _git("symbolic-ref", "HEAD", "refs/heads/main")
    _git("remote", "add", "origin", REMOTE)
    # Global config forces GPG signing, which needs an interactive passphrase
    # prompt — that would make every unattended commit here fail.
    _git("config", "commit.gpgsign", "false")
    (REPO_DIR / "README.md").write_text(
        "# 喵喵亭 · 数据备份\n\n"
        "私有仓库，由 `diet-manager/scripts/backup_to_git.py` 每天自动提交。\n"
        "存文本 SQL dump 而非二进制 .db，这样 git 能做增量压缩、且网页上能看 diff。\n\n"
        "## 恢复\n\n"
        "```bash\n"
        "# 恢复到最新状态\n"
        "sqlite3 diet.db < diet.sql\n\n"
        "# 恢复到某一天（先找到那天的提交）\n"
        "git log --oneline -- diet.sql\n"
        "git checkout <commit> -- diet.sql\n"
        "sqlite3 diet_restored.db < diet.sql\n"
        "```\n\n"
        "把恢复出的 `diet.db` 放回 `diet-manager/data/` 即可。\n"
        "`local_nutrition.json` 直接复制回 `diet-manager/data/`。\n",
        encoding="utf-8",
    )


def _dump_sql(dest: Path) -> None:
    """Consistent text dump of the live WAL-mode DB."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap.db"
        src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            tgt = sqlite3.connect(snap)
            try:
                src.backup(tgt)
            finally:
                tgt.close()
        finally:
            src.close()

        conn = sqlite3.connect(snap)
        try:
            with dest.open("w", encoding="utf-8") as f:
                for line in conn.iterdump():
                    f.write(line + "\n")
        finally:
            conn.close()


def main() -> None:
    _ensure_repo()

    _dump_sql(REPO_DIR / "diet.sql")
    for name in ("local_nutrition.json", "ingredient_translations.json"):
        src = DATA_DIR / name
        if src.exists():
            shutil.copy2(src, REPO_DIR / name)

    _git("add", "-A")
    if not _git("status", "--porcelain").stdout.strip():
        print("数据无变化，跳过提交")
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    _git("commit", "-q", "-m", f"Data snapshot {stamp}")
    _git("push", "-q", "-u", "origin", "main")
    print(f"已推送数据快照 {stamp}")


if __name__ == "__main__":
    main()
