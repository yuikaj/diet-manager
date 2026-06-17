"""
下厨房菜谱一次性爬取脚本 (Playwright 增强版 - 极简诚实搬运模式)

Usage:
    python3.9 scripts/scrape_xiachufang.py --user-id 11040027 --session-cookie "S=xxx" --dry-run
    python3.9 scripts/scrape_xiachufang.py --user-id 11040027 --session-cookie "S=xxx"

遭遇 502/429 error 清理命令：
    python3.9 -c "from db.init_db import get_connection; conn = get_connection(); conn.execute(\"DELETE FROM recipes WHERE name = '502 Bad Gateway' OR name LIKE '%429%'\"); conn.commit(); print('✅ 脏数据清理完毕！')"
"""
import argparse
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, BrowserContext

from db.init_db import init_database
from db.recipes import create_recipe, recipe_exists_by_url

BASE_URL      = "https://www.xiachufang.com"
DELAY_SEC     = 5.0   # 基础延迟改大到 5 秒，防 429 封锁
DELAY_JITTER  = 3.0   # 随机波动 3 秒
UA            = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ─── Playwright HTTP helper ───────────────────────────────────

def _sleep():
    time.sleep(DELAY_SEC + random.uniform(0, DELAY_JITTER))

def _get_html_with_playwright(page: Page, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    """使用 Playwright 获取页面，遇验证码拦截并等待人工接管"""
    for attempt in range(retries):
        try:
            page.goto(url, timeout=45000)

            if "/auth/login/" in page.url:
                print("  🔒 重定向到登录页：cookie 可能已失效或未提供")
                return None

            page_title = page.title()
            if "滑动验证" in page_title or "滑块" in page_title or "验证码" in page_title:
                print(f"\n  🧩 触发滑动验证拦截 (尝试 {attempt+1}/{retries})")
                print("  👉 请在弹出的浏览器窗口中手动拉动滑块完成验证...")

                try:
                    page.wait_for_selector(".topbar, .site-nav", timeout=120000)
                    print("  ✅ 验证通过，继续抓取...")
                    time.sleep(2)
                except Exception:
                    print("  ⚠️ 等待人工验证超时，准备重试...")
                    continue

            html = page.content()
            return BeautifulSoup(html, "lxml")

        except Exception as e:
            print(f"  ⚠️  页面获取异常: {url} ({e})")
            if attempt < retries - 1:
                time.sleep(5)

    print(f"  ❌ 已放弃抓取: {url}")
    return None


# ─── Recipe list ──────────────────────────────────────────────

def _get_recipe_urls(page: Page, user_id: str) -> list:
    url_set: set = set()
    urls:    list = []
    page_num = 1

    while True:
        list_url = f"{BASE_URL}/cook/{user_id}/created/?page={page_num}"
        print(f"  获取第 {page_num} 页: {list_url}")
        soup = _get_html_with_playwright(page, list_url)

        if not soup:
            print(f"  ⚠️  第 {page_num} 页获取失败，停止列表抓取")
            break

        on_page = []
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if re.match(r"^/recipe/\d+/?$", href):
                full = BASE_URL + href.rstrip("/") + "/"
                if full not in on_page:
                    on_page.append(full)

        new_count = 0
        for u in on_page:
            if u not in url_set:
                url_set.add(u)
                urls.append(u)
                new_count += 1

        print(f"  第 {page_num} 页: {len(on_page)} 个链接，新增 {new_count} 道，累计 {len(urls)} 道")

        page_nums = []
        for a in soup.select("a[href*='page=']"):
            m = re.search(r"page=(\d+)", a.get("href", ""))
            if m:
                page_nums.append(int(m.group(1)))

        max_page = max(page_nums) if page_nums else page_num
        if page_num >= max_page:
            print(f"  已到最后一页（第 {max_page} 页），列表抓取完毕")
            break

        page_num += 1
        _sleep()

    return urls


# ─── Single recipe ────────────────────────────────────────────

def _scrape_recipe(page: Page, url: str) -> Optional[dict]:
    soup = _get_html_with_playwright(page, url)
    if not soup:
        return None

    title_el = soup.select_one("h1")
    if not title_el:
        page_title = soup.select_one("title")
        pt = page_title.get_text(strip=True) if page_title else "unknown"
        print(f"    ⚠️  找不到标题 (页面title='{pt}'): {url}")
        return None

    name = title_el.get_text(strip=True)

    # --- 提取食材 (彻底原味保留) / Extract Ingredients ---
    ingredients = []

    for row in soup.select(".ings table tr"):
        name_td = row.select_one("td.name")
        unit_td = row.select_one("td.unit")
        if not name_td:
            continue

        a_tag = name_td.select_one("a")
        # 连括号备注一起完整保留
        ing_name = (a_tag or name_td).get_text(strip=True)

        raw_amount = unit_td.get_text(strip=True) if unit_td else ""
        if not raw_amount:
            raw_amount = "需要AI从主食材名中提取"

        ingredients.append({
            "name":         ing_name,
            "amount":       0.0,           # 临时填 0.0，等 AI 洗
            "unit":         raw_amount,    # 原始文本直接存进 unit 字段
            "is_condiment": False,         # 交给 AI 重新分类
            "intake_ratio": 1.0,
        })

    # 全部交由 AI 进行统一清洗
    data_quality = "needs_review"

    # --- 提取步骤 / Extract Steps ---
    steps_list = []
    steps_el = soup.select_one(".steps")
    if steps_el:
        list_items = steps_el.select("li.container, li")
        for item in list_items:
            p_text = item.select_one("p.text")
            step_content = p_text.get_text(" ", strip=True) if p_text else item.get_text(" ", strip=True)

            if step_content:
                step_content = re.sub(r'^(步骤\s*)?\d+\.?\s*', '', step_content)
                steps_list.append(step_content)

    method_map = [("炒", "炒"), ("蒸", "蒸"), ("烤", "烤"), ("炖", "炖"),
                  ("煮", "煮"), ("炸", "炸"), ("煎", "煎"), ("拌", "凉拌"),
                  ("焖", "炖"), ("卤", "煮")]
    methods = [m for kw, m in method_map if kw in name]
    if not methods:
        methods = ["炒"]

    uses_wok   = any(m in methods for m in ["炒", "煎", "炸"])
    is_parallel = any(m in methods for m in ["蒸", "烤", "炖", "凉拌"])

    form_tags = []
    if any(kw in name for kw in ["汤", "羹", "煲"]):
        form_tags.append("汤")
    if any(kw in name for kw in ["拌", "凉拌", "凉"]):
        form_tags.append("凉拌")
    category = ["荤菜"] + form_tags

    notes = "[提示] 数据等待 AI 处理整合。"

    return {
        "recipe": {
            "name":           name,
            "source_url":     url,
            "cooking_method": methods,
            "uses_wok":       uses_wok,
            "is_parallel":    is_parallel,
            "prep_difficulty":"中等",
            "cook_time_min":  None,
            "category":       category,
            "tags":           [],
            "data_quality":   data_quality,
            "notes":          notes,
            "steps":          steps_list,
        },
        "ingredients": ingredients,
    }


# ─── Main ─────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="下厨房菜谱爬取（Playwright 极简诚实搬运模式）")
    ap.add_argument("--user-id",       required=True, help="下厨房用户数字 ID，如 11040027")
    ap.add_argument("--session-cookie",default="",    help='登录 cookie，格式：S=eyJ...')
    ap.add_argument("--dry-run",       action="store_true", help="只打印，不写入数据库")
    ap.add_argument("--limit",         type=int, default=0,  help="仅抓前 N 道，0=全部")
    ap.add_argument("--resume",        default="",    help="从指定 URL 开始续抓")
    args = ap.parse_args()

    print("🥢 下厨房爬虫启动 (Playwright 模式)")
    print(f"   用户 ID       : {args.user_id}")
    print(f"   cookie 已提供 : {'是' if args.session_cookie else '否 ⚠️'}")
    print()

    if not args.dry_run:
        init_database()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=UA)

        # 👇 反检测脚本，抹除 WebDriver 标记
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.navigator.chrome = {
                runtime: {},
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3],
            });
        """)

        if args.session_cookie:
            for kv in args.session_cookie.split(";"):
                k, _, v = kv.strip().partition("=")
                if k.strip() == "S" and v.strip():
                    context.add_cookies([{
                        "name": "S",
                        "value": v.strip(),
                        "domain": ".xiachufang.com",
                        "path": "/"
                    }])
                    print("🍪 Session cookie 已注入")

        page = context.new_page()

        print("📋 获取菜谱列表... \n")
        recipe_urls = _get_recipe_urls(page, args.user_id)
        total = len(recipe_urls)

        if not recipe_urls:
            print("❌ 未找到菜谱，可能是 Cookie 失效。")
            browser.close()
            return

        if args.limit:
            recipe_urls = recipe_urls[: args.limit]
            print(f"（限制抓取前 {args.limit} 道）")

        if args.resume:
            if args.resume in recipe_urls:
                idx = recipe_urls.index(args.resume)
                recipe_urls = recipe_urls[idx:]
                print(f"（从第 {idx+1} 道续抓，共剩 {len(recipe_urls)} 道）")

        saved = skipped = failed = 0

        for i, url in enumerate(recipe_urls, 1):
            print(f"\n[{i}/{len(recipe_urls)}] {url}")

            if not args.dry_run and recipe_exists_by_url(url):
                print("   ⏭  数据库中已存在，跳过")
                skipped += 1
                continue

            data = _scrape_recipe(page, url)
            if not data:
                failed += 1
                continue

            r    = data["recipe"]
            ings = data["ingredients"]
            steps= r.get("steps", [])
            print(f"   ✅ {r['name']}  ({len(ings)} 食材, {len(steps)} 个步骤, quality={r['data_quality']})")

            if args.dry_run:
                if ings: print(f"      示例食材: {ings[0]['name']} -> {ings[0]['amount']}{ings[0]['unit']}")
                if steps:print(f"      首个步骤: {steps[0][:30]}...")
            else:
                create_recipe(r, ings)
                saved += 1

            _sleep()

        print("\n" + "=" * 50)
        print(f"完成抓取任务：保存 {saved} ｜ 跳过 {skipped} ｜ 失败 {failed}")
        browser.close()

if __name__ == "__main__":
    main()