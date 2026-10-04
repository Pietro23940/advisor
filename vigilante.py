#!/usr/bin/env python3
"""Lee los precios de products.json, avisa por ntfy si bajan y genera data/precios.js."""
import json, os, re, random, statistics, time, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "precios.json"
OUT_JS = ROOT / "data" / "precios.js"
IMG_DIR = ROOT / "img"
TOPIC = os.getenv("NTFY_TOPIC", "").strip()
DELAY = float(os.getenv("DELAY", "4"))  # segundos entre peticiones (aprox.)
IVA = 1.21  # Amazon.es enseña precios sin IVA a visitantes de fuera de la UE (p. ej. el proxy)
MAX_RATIO = 2.5   # precio > 2,5× (o < 1/2,5) la mediana de las otras tiendas -> sospechoso
MAX_JUMP = 0.6    # cambio > 60 % respecto al anterior -> se confirma en la siguiente pasada
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def parse_price(txt):
    if txt is None:
        return None
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
    return v if 0.5 <= v < 20000 else None

def is_blocked(r):
    low = r.text[:5000].lower()
    if "just a moment" in low or r.headers.get("cf-mitigated"):
        return "protegido (Cloudflare)"
    if r.status_code in (403, 429, 503):
        return f"bloqueado ({r.status_code})"
    return None

# ---------- Amazon ----------

def _block(html, elem_id, size=6000):
    i = html.find(f'id="{elem_id}"')
    return html[i:i + size] if i >= 0 else ""

def amazon_buybox(html):
    """Precio de la caja de compra (solo el del producto de la página, no anuncios, variantes ni PVP tachado)."""
    for bid in ("corePriceDisplay_desktop_feature_div", "apex_desktop"):
        m = re.search(r'(?:priceToPay|apexPriceToPay)[^>]*>\s*<span class="a-offscreen">\s*([^<]+)<', _block(html, bid))
        if m and parse_price(m.group(1)):
            return parse_price(m.group(1))
    for bid in ("corePrice_feature_div", "corePriceDisplay_desktop_feature_div", "apex_desktop"):
        for m in re.finditer(r'class="a-offscreen">\s*([^<]+)<', _block(html, bid)):
            p = parse_price(m.group(1))
            if p:
                return p
    m = re.search(r'"priceAmount":\s*([\d.]+)', html)
    return parse_price(m.group(1)) if m else None

def amazon_image(html):
    m = re.search(r'data-old-hires="(https://m\.media-amazon\.com/images/I/[^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'id="landingImage"[^>]*data-a-dynamic-image="\{&quot;(https://[^&]+)&quot;', html)
    return m.group(1) if m else None

def amazon_offers(asin):
    """Oferta nueva más barata de «Ver todas las opciones de compra» (cuando Amazon oculta la caja de compra)."""
    r = SESSION.get(f"https://www.amazon.es/gp/product/ajax/aodAjaxMain/?asin={asin}&pc=dp&experienceId=aodAjaxMain",
                    headers={"Referer": f"https://www.amazon.es/dp/{asin}", "X-Requested-With": "XMLHttpRequest"},
                    timeout=20)
    prices = []
    for seg in re.split(r'id="aod-offer"', r.text)[1:]:
        text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>|\s+", " ", seg[:15000], flags=re.S)
        m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€", text)
        if not m or not re.search(r"\bNuevo\b|\bNew\b", text[:m.start()]):
            continue
        p = parse_price(m.group(1))
        if p:
            prices.append(p)
    return min(prices) if prices else None

def amazon_direct(url, asin):
    r = SESSION.get(url, timeout=20)
    low = r.text.lower()
    if r.status_code != 200 or "captcha" in low or "robot check" in low:
        return None, "bloqueado", None
    html = r.text
    price = amazon_buybox(html)
    if price is None:
        time.sleep(1.5)
        price = amazon_offers(asin)
    return price, None if price else "no disponible en Amazon", html

def amazon_proxy(url):
    """Vía r.jina.ai (para cuando GitHub Actions está bloqueado). Entra desde fuera de la UE: precios sin IVA."""
    for intento in range(2):
        try:
            r = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "html"}, timeout=60)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200 and len(r.text) > 50000:
            html = r.text
            text = re.sub(r"<[^>]+>|\s+", " ", _block(html, "availability", 3000))
            if "Currently unavailable" in text or "No disponible" in text:
                return None, "sin oferta visible desde el proxy", html
            price = amazon_buybox(html)
            if price is None:
                return None, "precio no encontrado (proxy)", html
            deliver = re.search(r"(?:Deliver to|Enviar a|Entrega en)(?:&nbsp;|\s|<[^>]+>)*([^<]{0,40})", html)
            if not (deliver and re.search(r"Espa|Spain|\d{5}", deliver.group(1))):
                price = round(price * IVA, 2)
            return price, None, html
        time.sleep(5 * (intento + 1))
    return None, "proxy sin respuesta", None

def fetch_amazon(url, asin):
    """Devuelve (precio, error, fuente, html)."""
    try:
        price, err, html = amazon_direct(url, asin)
        if err != "bloqueado":
            return price, err, "directo", html
    except requests.RequestException:
        pass
    price, err, html = amazon_proxy(url)
    return price, err, "proxy", html

# ---------- Otras tiendas ----------

def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        if node.get("@type") in ("Product", "IndividualProduct", "Offer") and "offers" in node:
            offers = node["offers"]
            for o in (offers if isinstance(offers, list) else [offers]):
                if isinstance(o, dict):
                    p = parse_price(o.get("price") or o.get("lowPrice") or "")
                    if p:
                        yield p
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)

def extract_price(html):
    for pat in (r'property="product:price:amount"\s+content="([^"]+)"',
                r'content="([^"]+)"\s+property="product:price:amount"',
                r'itemprop="price"\s+content="([^"]+)"',
                r'content="([^"]+)"\s+itemprop="price"'):
        m = re.search(pat, html, re.I)
        if m and parse_price(m.group(1)):
            return parse_price(m.group(1))
    for m in re.finditer(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            for p in _walk(json.loads(m.group(1))):
                return p
        except (json.JSONDecodeError, ValueError):
            continue
    return None

def in_stock(html):
    m = re.search(r'product:availability"\s+content="([^"]+)"', html, re.I)
    if m:
        return "out" not in m.group(1).lower()
    m = re.search(r'schema\.org/(InStock|OutOfStock|SoldOut|Discontinued)', html)
    return m.group(1) == "InStock" if m else None

def store_proxy(url):
    """Plan B cuando la tienda bloquea la IP (p. ej. Coolmod desde GitHub Actions)."""
    try:
        r = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "html"}, timeout=60)
    except requests.RequestException:
        return None
    if r.status_code != 200 or "just a moment" in r.text[:5000].lower():
        return None
    price = extract_price(r.text)
    return (price, None, in_stock(r.text)) if price else None

def fetch_store(url):
    """Devuelve (precio, error, en_stock)."""
    try:
        r = SESSION.get(url, timeout=25)
    except requests.RequestException as e:
        return store_proxy(url) or (None, f"red: {type(e).__name__}", None)
    blocked = is_blocked(r)
    if blocked == "protegido (Cloudflare)":  # el proxy tampoco pasa el reto de Cloudflare
        return None, blocked, None
    if blocked:
        return store_proxy(url) or (None, blocked, None)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}", None
    price = extract_price(r.text)
    if price:
        return price, None, in_stock(r.text)
    # Algunas tiendas cargan reCAPTCHA en todas las fichas; solo cuenta como bloqueo si no hay precio
    if "robot check" in r.text.lower() or "captcha" in r.text[:3000].lower():
        return store_proxy(url) or (None, "bloqueado (captcha)", None)
    return None, "precio no encontrado", None

# ---------- Imágenes ----------

def save_image(pid, img_url):
    dest = IMG_DIR / f"{pid}.jpg"
    if dest.exists() and dest.stat().st_size > 1024:
        return
    if not img_url:
        return
    img_url = re.sub(r"\._[^/]*_\.jpg$", "._AC_SL500_.jpg", img_url)
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=20)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/jpeg") and len(r.content) > 1024:
            IMG_DIR.mkdir(exist_ok=True)
            dest.write_bytes(r.content)
            print(f"[{pid}] imagen guardada")
    except requests.RequestException as e:
        print(f"[{pid}] no se pudo descargar la imagen: {e}")

# ---------- Avisos ----------

def notify(title, body, click=None, tags="moneybag"):
    print(f"AVISO [{title}]: {body}")
    if not TOPIC:
        return
    headers = {"Title": title, "Tags": tags}
    if click:
        headers["Click"] = click
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=body.encode("utf-8"), headers=headers, timeout=15)
    except requests.RequestException as e:
        print("No se pudo enviar el aviso:", e)

def suspicious(price, others, rec):
    """Motivo por el que un precio no es fiable, o None."""
    if len(others) >= 2:
        med = statistics.median(others)
        if price > med * MAX_RATIO or price < med / MAX_RATIO:
            return f"precio sospechoso ({price:.2f} € frente a {med:.2f} € en otras tiendas)"
    old = rec.get("precio") or rec.get("ultimo")
    if old and abs(price - old) / old > MAX_JUMP and rec.get("pendiente") != price:
        rec["pendiente"] = price
        return f"cambio brusco ({old:.2f} → {price:.2f} €), pendiente de confirmar"
    return None

def main():
    if not TOPIC:
        print("::warning::NTFY_TOPIC está vacío: no se enviarán avisos al móvil")
    elif os.getenv("PRUEBA_AVISO") == "true":
        notify("Prueba del vigilante", "Si ves esto, los avisos de bajada de precio te llegarán al móvil.",
               "https://pietro23940.github.io/advisor/", "white_check_mark")
    conf = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    min_drop = float(conf.get("ajustes", {}).get("bajada_minima_pct", 1))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    first = True

    for p in conf["productos"]:
        item = state.setdefault(p["id"], {})
        for store in list(item):
            if not p["tiendas"].get(store):
                del item[store]
        found = {}
        for store, url in p["tiendas"].items():
            if not url:
                continue
            if not first:
                time.sleep(DELAY * random.uniform(0.7, 1.3))
            first = False

            rec = item.setdefault(store, {})
            if rec.get("url") != url:  # enlace nuevo o cambiado: su historial ya no vale
                rec.clear()
            rec.setdefault("historial", [])
            if store == "amazon":
                price, err, fuente, html = fetch_amazon(url, p["asin"])
                rec["fuente"] = fuente
                if html:
                    save_image(p["id"], amazon_image(html))
            else:
                price, err, stock = fetch_store(url)
                rec["stock"] = stock
            rec.update(url=url, comprobado=now, error=err)
            found[store] = price

        # Validar y guardar después de leer todas las tiendas, para poder comparar entre ellas
        for store, price in found.items():
            rec = item[store]
            if price is not None:
                others = [v for s, v in found.items() if s != store and v is not None]
                if rec.get("stock") is False:
                    print(f"[{p['id']}/{store}] sin stock")
                why = suspicious(price, others, rec)
                if why:
                    rec["error"], price = why, None
            if price is None:
                if rec.get("precio") is not None:
                    rec["ultimo"], rec["ultimo_fecha"] = rec["precio"], rec.get("fecha_precio", now)
                rec["precio"] = None
                print(f"[{p['id']}/{store}] sin precio: {rec['error']}")
                continue

            rec.pop("pendiente", None)
            old = rec.get("precio") if rec.get("precio") is not None else rec.get("ultimo")
            rec.update(precio=price, fecha_precio=now)
            rec.pop("ultimo", None)
            rec.pop("ultimo_fecha", None)
            if old != price:
                rec["historial"] = (rec["historial"] + [[now, price]])[-200:]
            print(f"[{p['id']}/{store}] {price:.2f} €")

            target = p.get("objetivo")
            dropped = old is not None and price <= old * (1 - min_drop / 100)
            hit_target = target and price <= target and (old is None or old > target)
            if (dropped or hit_target) and rec.get("stock") is not False:
                why = f"objetivo {target:.2f} € alcanzado" if hit_target and not dropped else f"antes {old:.2f} €"
                notify("Bajada de precio", f"{p['nombre']}: {price:.2f} € en {store} ({why})", rec["url"], "chart_with_downwards_trend")

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = {"actualizado": now, "productos": conf["productos"], "precios": state}
    OUT_JS.write_text("window.DATOS = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")

if __name__ == "__main__":
    main()
