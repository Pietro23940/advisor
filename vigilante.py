#!/usr/bin/env python3
"""Lee los precios de products.json, avisa por ntfy si bajan y genera data/precios.js."""
import json, os, re, random, time, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "precios.json"
OUT_JS = ROOT / "data" / "precios.js"
TOPIC = os.getenv("NTFY_TOPIC", "").strip()
DELAY = float(os.getenv("DELAY", "4"))  # segundos entre peticiones (aprox.)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

def parse_price(txt):
    s = re.sub(r"[^\d.,]", "", str(txt))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 1 <= v < 20000 else None

def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        if "offers" in node:
            offers = node["offers"]
            for o in (offers if isinstance(offers, list) else [offers]):
                if isinstance(o, dict):
                    p = parse_price(o.get("price") or o.get("lowPrice") or "")
                    if p:
                        yield p
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)

def extract_price(html, store):
    if store == "amazon":
        m = re.search(r'class="a-offscreen">\s*([\d.,]+)\s*(?:€|&euro;)', html)
        if m and parse_price(m.group(1)):
            return parse_price(m.group(1))
    for m in re.finditer(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            for p in _walk(json.loads(m.group(1))):
                return p
        except (json.JSONDecodeError, ValueError):
            continue
    for pat in (r'(?:product:price:amount|og:price:amount)["\']\s+content=["\']([^"\']+)',
                r'content=["\']([^"\']+)["\']\s+(?:property|name)=["\'](?:product:price:amount|og:price:amount)',
                r'itemprop=["\']price["\']\s+content=["\']([^"\']+)',
                r'content=["\']([^"\']+)["\']\s+itemprop=["\']price'):
        m = re.search(pat, html, re.I)
        if m and parse_price(m.group(1)):
            return parse_price(m.group(1))
    return None

def fetch_price(url, store):
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException as e:
        return None, f"red: {type(e).__name__}"
    low = r.text.lower()
    if r.status_code in (403, 429, 503) or "captcha" in low or "robot check" in low:
        return None, f"bloqueado ({r.status_code})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    price = extract_price(r.text, store)
    return (price, None) if price else (None, "precio no encontrado")

def notify(title, body, click):
    print("AVISO:", body)
    if not TOPIC:
        return
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=body.encode("utf-8"),
                      headers={"Title": title, "Tags": "moneybag", "Click": click}, timeout=15)
    except requests.RequestException as e:
        print("No se pudo enviar el aviso:", e)

def main():
    conf = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    min_drop = float(conf.get("ajustes", {}).get("bajada_minima_pct", 1))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    first = True
    for p in conf["productos"]:
        item = state.setdefault(p["id"], {})
        for store, url in p["tiendas"].items():
            if not url:
                continue
            if not first:
                time.sleep(DELAY * random.uniform(0.7, 1.5))
            first = False
            price, err = fetch_price(url, store)
            rec = item.setdefault(store, {"precio": None, "historial": []})
            rec.update(url=url, comprobado=now, error=err)
            if price is None:
                print(f"[{p['id']}/{store}] sin precio: {err}")
                continue
            old = rec["precio"]
            rec["precio"] = price
            if old != price:
                rec["historial"] = (rec["historial"] + [[now, price]])[-100:]
            print(f"[{p['id']}/{store}] {price:.2f} €")
            target = p.get("objetivo")
            dropped = old is not None and price <= old * (1 - min_drop / 100)
            hit_target = target and price <= target and (old is None or old > target)
            if dropped or hit_target:
                why = f"objetivo {target:.2f} € alcanzado" if hit_target and not dropped else f"antes {old:.2f} €"
                notify("Bajada de precio", f"{p['nombre']}: {price:.2f} € en {store} ({why})", url)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = {"actualizado": now, "productos": conf["productos"], "precios": state}
    OUT_JS.write_text("window.DATOS = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")

if __name__ == "__main__":
    main()
