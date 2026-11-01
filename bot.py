"""בוט טלגרם: מקבל קישור -> Claude כותב עמוד מוצר -> commit + push -> GitHub Actions מפרסם."""
import os, re, json, shutil, subprocess, datetime, pathlib, logging
import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

ROOT = pathlib.Path(__file__).parent
ALLOWED = int(os.environ["ALLOWED_USER_ID"])      # רק אתה יכול להפעיל את הבוט
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
IMG_RE = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)
claude = Anthropic()
logging.basicConfig(level=logging.INFO)

SYSTEM = """אתה כותב תוכן לעמודי מוצר באתר שיווק שותפים בעברית.
כללים: השתמש אך ורק במידע שסופק לך. אל תמציא מחיר, מפרט, דירוג, מבצע או תכונה.
אם חסר מידע, כתוב בצורה כללית וזהירה. טון ענייני וידידותי, בלי הגזמות ובלי סופרלטיבים ריקים.
החזר JSON בלבד, בלי טקסט נוסף, במבנה:
{"slug":"english-kebab-case","title":"...","tagline":"משפט אחד","description":["פסקה","פסקה"],"features":["...","..."],"category":"..."}"""

def page_info(url):
    """ניסיון best-effort לשלוף מידע מהדף. AliExpress לפעמים חוסם, ואז מחזירים ריק."""
    try:
        r = requests.get(url, headers=UA, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        og = lambda k: (s.find("meta", property=k) or {}).get("content", "")
        return {"final_url": r.url, "title": og("og:title") or (s.title.string if s.title else ""),
                "description": og("og:description"), "image": og("og:image"),
                "text": s.get_text(" ", strip=True)[:3000]}
    except Exception as e:
        logging.warning("fetch failed: %s", e)
        return {}

def generate(notes, info):
    msg = claude.messages.create(model=MODEL, max_tokens=1500, system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps({"notes": notes, "page": info}, ensure_ascii=False)}])
    raw = msg.content[0].text
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])

def save_image(slug, n, content):
    d = ROOT / "images" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n}.jpg").write_bytes(content)
    return f"images/{slug}/{n}.jpg"

def publish(message):
    run = lambda *a: subprocess.run(a, cwd=ROOT, check=True)
    run("git", "add", "-A")
    run("git", "commit", "-m", message)
    run("git", "pull", "--rebase")
    run("git", "push")

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if not m or m.from_user.id != ALLOWED:
        return
    text = m.text or m.caption or ""
    links = re.findall(r"https?://\S+", text)
    img_urls = [u for u in links if IMG_RE.search(u)]
    urls = [u for u in links if u not in img_urls]
    if not urls:
        await m.reply_text("שלח קישור שותפים. אפשר להוסיף הערות, תמונות וקישורי תמונה באותה הודעה.")
        return
    url = urls[0]
    notes = text.replace(url, "").strip()
    await m.reply_text("עובד על העמוד...")
    info = page_info(url)
    data = generate(notes, info)
    slug = re.sub(r"[^a-z0-9-]", "", data.get("slug", "").lower()) or datetime.datetime.now().strftime("p%Y%m%d%H%M%S")
    images = []
    if m.photo:
        f = await ctx.bot.get_file(m.photo[-1].file_id)
        images.append(save_image(slug, len(images) + 1, bytes(await f.download_as_bytearray())))
    for u in img_urls + ([info["image"]] if info.get("image") else []):
        try:
            images.append(save_image(slug, len(images) + 1, requests.get(u, headers=UA, timeout=20).content))
        except Exception as e:
            logging.warning("image failed: %s", e)
    data.update(slug=slug, url=url, images=images, date=datetime.date.today().isoformat())
    (ROOT / "products" / f"{slug}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    publish(f"add product {slug}")
    await m.reply_text(f"פורסם (לוקח כדקה): {SITE_URL}/p/{slug}/index.html\nלמחיקה: /delete {slug}")

async def on_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if m.from_user.id != ALLOWED or not ctx.args:
        return
    slug = re.sub(r"[^a-z0-9-]", "", ctx.args[0])
    (ROOT / "products" / f"{slug}.json").unlink(missing_ok=True)
    shutil.rmtree(ROOT / "images" / slug, ignore_errors=True)
    publish(f"remove product {slug}")
    await m.reply_text(f"הוסר: {slug}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
    app.add_handler(CommandHandler("delete", on_delete))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_message))
    app.run_polling()
