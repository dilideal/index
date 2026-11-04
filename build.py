"""בונה את האתר הסטטי: products/*.json + templates/ -> dist/"""
import json, shutil, pathlib
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "dist"
site = json.loads((ROOT / "site.json").read_text("utf-8"))

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir()
shutil.copytree(ROOT / "static", OUT / "static")
if (ROOT / "images").exists():
    shutil.copytree(ROOT / "images", OUT / "images")

env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
products = sorted(
    (json.loads(f.read_text("utf-8")) for f in (ROOT / "products").glob("*.json")),
    key=lambda p: p.get("date", ""), reverse=True,
)

order_file = ROOT / "order.json"
order = json.loads(order_file.read_text("utf-8-sig")).get("order", []) if order_file.exists() else []
pos = {slug: i for i, slug in enumerate(order)}
# מוצרים שלא ברשימת הסדר (חדשים) מופיעים ראשונים, אחריהם לפי הסדר שנקבע
products.sort(key=lambda p: (p["slug"] in pos, pos.get(p["slug"], 0)))

def render(template, path, **ctx):
    dest = OUT / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(env.get_template(template).render(site=site, **ctx), "utf-8")

render("index.html", "index.html", products=products, root="")
for p in products:
    render("product.html", f"p/{p['slug']}/index.html", p=p, root="../../")
print(f"built {len(products)} product pages -> {OUT}")
