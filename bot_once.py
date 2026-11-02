"""רץ מתוך GitHub Actions: שולף הודעות חדשות מטלגרם, יוצר או מוחק עמודי מוצר, ויוצא.
את ה-commit והפרסום עושה ה-workflow (.github/workflows/bot.yml)."""
import os, re, json, shutil, datetime, pathlib, logging
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).parent
TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED = int(os.environ["ALLOWED_USER_ID"])
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
XAI_KEY = os.environ["XAI_API_KEY"]
MODEL = os.environ.get("GROK_MODEL") or "grok-4"
API = f"https://api.telegram.org/bot{TOKEN}"
FILES = f"https://api.telegram.org/file/bot{TOKEN}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
IMG_RE = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)
logging.basicConfig(level=logging.INFO)

SYSTEM = """אתה כותב תוכן לעמודי מוצר באתר שיווק שותפים בעברית.
כללים: השתמש אך ורק במידע שסופק לך. אל תמציא מחיר, מפרט, דירוג, מבצע או תכונה.
אם חסר מידע, כתוב בצורה כללית וזהירה. טון ענייני וידידותי, בלי הגזמות ובלי סופרלטיבים ריקים.
החזר JSON בלבד, בלי טקסט נוסף, במבנה:
{"slug":"english-kebab-case","title":"...","tagline":"משפט אחד","description":["פסקה","פסקה"],"features":["...","..."],"category":"..."}"""

def tg(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=40)
    r.raise_for_status()
    return r.json()["result"]

def reply(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)

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
    r = requests.post("https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {XAI_KEY}"}, timeout=120,
        json={"model": MODEL, "temperature": 0.4, "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"notes": notes, "page": info}, ensure_ascii=False)}]})
    if not r.ok:
        raise RuntimeError(f"Grok API {r.status_code}: {r.text[:200]}")
    raw = r.json()["choices"][0]["message"]["content"]
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])

def save_image(slug, n, content):
    d = ROOT / "images" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n}.jpg").write_bytes(content)
    return f"images/{slug}/{n}.jpg"

def handle(m):
    chat = m["chat"]["id"]
    text = m.get("text") or m.get("caption") or ""

    if text.startswith("/delete"):
        slug = re.sub(r"[^a-z0-9-]", "", text[len("/delete"):].strip().lower())
        f = ROOT / "products" / f"{slug}.json"
        if not slug or not f.exists():
            reply(chat, "שימוש: /delete slug (ה-slug מופיע בכתובת העמוד). לא נמצא מוצר כזה.")
            return
        f.unlink()
        shutil.rmtree(ROOT / "images" / slug, ignore_errors=True)
        reply(chat, f"הוסר: {slug}. האתר יתעדכן בעוד כמה דקות.")
        return

    links = re.findall(r"https?://\S+", text)
    img_urls = [u for u in links if IMG_RE.search(u)]
    urls = [u for u in links if u not in img_urls]
    if not urls:
        reply(chat, "שלח קישור שותפים. אפשר להוסיף הערות, תמונה וקישורי תמונה באותה הודעה.")
        return
    url = urls[0]
    notes = text.replace(url, "").strip()
    reply(chat, "עובד על העמוד...")
    info = page_info(url)
    data = generate(notes, info)
    slug = re.sub(r"[^a-z0-9-]", "", data.get("slug", "").lower()) or datetime.datetime.now().strftime("p%Y%m%d%H%M%S")

    images = []
    if m.get("photo"):
        path = tg("getFile", file_id=m["photo"][-1]["file_id"])["file_path"]
        images.append(save_image(slug, len(images) + 1, requests.get(f"{FILES}/{path}", timeout=30).content))
    for u in img_urls + ([info["image"]] if info.get("image") else []):
        try:
            images.append(save_image(slug, len(images) + 1, requests.get(u, headers=UA, timeout=20).content))
        except Exception as e:
            logging.warning("image failed: %s", e)

    data.update(slug=slug, url=url, images=images, date=datetime.date.today().isoformat())
    (ROOT / "products" / f"{slug}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    reply(chat, f"העמוד נשמר ויתפרסם בעוד כמה דקות:\n{SITE_URL}/p/{slug}/index.html\nלמחיקה: /delete {slug}")

def main():
    updates = tg("getUpdates", timeout=0, allowed_updates=["message"])
    logging.info("%d new updates", len(updates))
    for u in updates:
        m = u.get("message")
        try:
            if m and m.get("from", {}).get("id") == ALLOWED:
                handle(m)
        except Exception as e:
            logging.exception("update failed")
            reply(m["chat"]["id"], f"שגיאה: {e}")
        finally:
            tg("getUpdates", offset=u["update_id"] + 1, timeout=0)  # מאשר לטלגרם שהעדכון טופל

if __name__ == "__main__":
    main()
