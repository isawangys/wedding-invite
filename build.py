#!/usr/bin/env python3
"""讀取出席表單 CSV，為每位確定出席的賓客產生專屬靜態頁。

用法：
  python3 build.py guests.csv --base-url https://your-site.pages.dev

輸出：
  docs/g/<token>.html   每人一頁（只含姓名與出席人數，不含飲食／兒童）
  docs/assets/          共用圖片
  docs/_headers         Cloudflare Pages／Netlify 用；GitHub Pages 不支援，改靠頁面內的 noindex meta
  links.csv             名字,連結（用來逐一用 LINE 傳送，勿公開）
  tokens.json           名字→token 對照；重跑時沿用，連結不會變
"""
import argparse
import csv
import html
import json
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 去掉易混淆字元


def new_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(10))


def find_col(header, *needles):
    """回傳第一個同時包含所有關鍵字的欄位索引，找不到回傳 None。"""
    for i, h in enumerate(header):
        if all(n in h for n in needles):
            return i
    return None


def to_int(s):
    try:
        return int(float(str(s).strip()))
    except ValueError:
        return 0


def esc(s):
    return html.escape(str(s).strip(), quote=True)


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    # 表單回覆 1 的第 1 列是統計列，標題列在第 2 列，所以自動尋找含「名字」的那一列
    for idx, r in enumerate(rows):
        if "名字" in r:
            return r, rows[idx + 1:]
    sys.exit("找不到標題列（需要有「名字」欄）")


def rsvp_items(g):
    items = []
    lunch = to_int(g["lunch"])
    if lunch > 0:  # 人數空白時不顯示，避免出現「0 位」；build 時會另外警告
        items.append(f'<li><span class="k">午宴出席</span>{lunch} 位</li>')

    if g["ceremony_choice"].startswith("僅參加午宴"):
        items.append('<li><span class="k">證婚儀式</span>僅參加午宴</li>')
    else:
        c = to_int(g["ceremony"])
        items.append(f'<li><span class="k">證婚儀式</span>參加{f"（{c} 位）" if c else ""}</li>')

    # 公開 repo 部署：刻意不放飲食與兒童資訊，降低個資暴露
    return "\n".join("        " + x for x in items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--base-url", default="", help="部署後的網址，用來產生 LINE 預覽圖與完整連結")
    ap.add_argument("--exclude", action="append", default=[], help="不要產生頁面的名字（可重複指定）")
    args = ap.parse_args()

    header, data = read_rows(args.csv)
    cols = {
        "ts": find_col(header, "時間戳記"),
        "name": find_col(header, "名字"),
        "attend": find_col(header, "是否能出席"),
        "ceremony_choice": find_col(header, "請問您是否參加"),
        "ceremony": find_col(header, "證婚儀式出席人數"),
        "lunch": find_col(header, "午宴總出席人數"),
        "diet": find_col(header, "飲食禁忌"),
        "has_kids": find_col(header, "是否有兒童"),
        "kid_total": find_col(header, "兒童數量"),
        "kid_s": find_col(header, "80公分以下"),
        "kid_m": find_col(header, "80-100"),
        "kid_l": find_col(header, "100-140"),
        "kid_seat": find_col(header, "兒童座椅"),
    }
    missing = [k for k, v in cols.items() if v is None]
    if missing:
        sys.exit(f"CSV 缺少欄位：{missing}")

    token_path = ROOT / "tokens.json"
    tokens = json.loads(token_path.read_text("utf-8")) if token_path.exists() else {}

    template = (ROOT / "template.html").read_text("utf-8")
    base = args.base_url.rstrip("/")
    og = f'<meta property="og:image" content="{base}/assets/1.webp">' if base else ""
    if not base:
        print("⚠️ 未提供 --base-url：不會輸出 og:image，LINE 預覽卡將沒有圖片", file=sys.stderr)

    out = ROOT / "docs"  # GitHub Pages 可直接以 main 分支的 /docs 發布
    if out.exists():
        shutil.rmtree(out)  # 只清自己的輸出目錄，避免殘留已移除賓客的頁面
    (out / "g").mkdir(parents=True)
    shutil.copytree(ROOT / "assets", out / "assets")
    (out / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n", "utf-8"
    )

    links, skipped, seen, no_lunch = [], [], {}, []
    for row in data:
        row = row + [""] * (len(header) - len(row))
        g = {k: row[i] for k, i in cols.items()}
        name = g["name"].strip()
        if not name or name in args.exclude:
            continue
        if not g["attend"].startswith("確定出席"):
            skipped.append((name, g["attend"].strip() or "（空白）"))
            continue
        seen[name] = seen.get(name, 0) + 1
        if to_int(g["lunch"]) == 0:
            no_lunch.append(name)

        key = f"{name}|{g['ts'].strip()}"  # 時間戳記讓同名兩筆也各有不同 token
        token = tokens.setdefault(key, new_token())
        page = (
            template.replace("{{NAME}}", esc(name))
            .replace("{{OG_IMAGE}}", og)
            .replace("{{RSVP_ITEMS}}", rsvp_items(g))
        )
        (out / "g" / f"{token}.html").write_text(page, "utf-8")
        links.append((name, f"{base}/g/{token}.html" if base else f"g/{token}.html"))

    token_path.write_text(json.dumps(tokens, ensure_ascii=False, indent=1), "utf-8")
    with open(ROOT / "links.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["名字", "連結"])
        w.writerows(links)

    print(f"✅ 已產生 {len(links)} 頁 → {out / 'g'}")
    print(f"   連結清單 → {ROOT / 'links.csv'}")
    if skipped:
        print(f"⚠️ 略過 {len(skipped)} 筆（非「確定出席」）：{skipped}")
    if no_lunch:
        print(f"⚠️ 確定出席但午宴人數為 0／空白，頁面不會顯示人數，請到表單補正：{no_lunch}")
    dups = [n for n, c in seen.items() if c > 1]
    if dups:
        print(f"⚠️ 同名出現多筆，請人工確認要保留哪一筆：{dups}")


if __name__ == "__main__":
    main()
