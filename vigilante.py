#!/usr/bin/env python3
"""Lee los precios de products.json, avisa por ntfy si bajan y genera data/precios.js."""
import json, os, re, random, statistics, sys, time, datetime as dt
from pathlib import Path
from urllib.parse import urlparse
import requests

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "precios.json"
OUT_JS = ROOT / "data" / "precios.js"
IMG_DIR = ROOT / "img"
TOPIC = os.getenv("NTFY_TOPIC", "").strip()
_TOPIC_FILE = Path.home() / ".config" / "vigilante-precios" / "ntfy_topic"
if not TOPIC and _TOPIC_FILE.exists():  # en el PC de casa, el tema se guarda fuera del repositorio
    TOPIC = _TOPIC_FILE.read_text(encoding="utf-8").strip()
DELAY = float(os.getenv("DELAY", "4"))  # segundos entre peticiones (aprox.)
IVA = 1.21  # Amazon.es enseña precios sin IVA a visitantes de fuera de la UE (p. ej. el proxy)
MAX_RATIO = 2.5   # precio > 2,5× (o < 1/2,5) la mediana de las otras tiendas -> sospechoso
MAX_JUMP = 0.6    # cambio > 60 % respecto al anterior -> se confirma en la siguiente pasada
MAX_AVISOS = 4    # si en una pasada hay más avisos que esto, se mandan resumidos en uno solo
PAGINA = os.getenv("PAGINA_URL", "https://pietro23940.github.io/advisor/")
NOMBRES = {"amazon": "Amazon.es", "pccomponentes": "PcComponentes", "neobyte": "Neobyte", "coolmod": "Coolmod",
           "pcbox": "PcBox", "alternate": "Alternate", "ldlc": "LDLC"}
DOMINIOS = {"amazon": "amazon.es", "pccomponentes": "pccomponentes.com", "neobyte": "neobyte.es", "coolmod": "coolmod.com",
            "pcbox": "pcbox.com", "alternate": "alternate.es", "ldlc": "ldlc.com"}
NO_DISPONIBLE = "no disponible en Amazon"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def http_get(url, sess=SESSION, tries=2, **kw):
    """GET que reintenta ante fallos de red y errores 500/502/504 pasajeros. Los bloqueos (403/429/503) no se
    reintentan: de ellos se ocupa quien llama (proxy, otro origen...)."""
    for i in range(tries):
        try:
            r = sess.get(url, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if i == tries - 1:
                raise
        else:
            if r.status_code not in (500, 502, 504) or i == tries - 1:
                return r
        time.sleep(2 * (i + 1))

def escribir_atomico(path, text):
    """Escribe el archivo entero o nada: si la pasada se corta a medias, el anterior queda intacto."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

def cargar_json(path, estricto=True):
    """Lee un JSON de estado. Si está corrupto se para (no se empieza de cero: perderíamos el historial)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        if estricto:
            sys.exit(f"::error::{path.name} está corrupto ({e}). Restáuralo con git (git checkout -- data/{path.name}).")
        print(f"::warning::{path.name} está corrupto y se ignora ({e})")
        return {}

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
    r = http_get(f"https://www.amazon.es/gp/product/ajax/aodAjaxMain/?asin={asin}&pc=dp&experienceId=aodAjaxMain",
                 headers={"Referer": f"https://www.amazon.es/dp/{asin}", "X-Requested-With": "XMLHttpRequest"},
                 timeout=20)
    if r.status_code != 200:
        return None
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

def asin_de(p):
    """ASIN del producto: el campo «asin» o, si falta, el que va en el enlace de Amazon (/dp/XXXXXXXXXX)."""
    if p.get("asin"):
        return p["asin"]
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", p.get("tiendas", {}).get("amazon") or "")
    return m.group(1) if m else None

EU = {"ES", "PT", "FR", "IT", "DE", "AT", "BE", "NL", "LU", "IE", "FI", "SE", "DK", "PL", "CZ", "SK", "SI",
      "HU", "RO", "BG", "HR", "GR", "CY", "MT", "EE", "LV", "LT"}

def amazon_country(html):
    """País desde el que Amazon cree que se visita (GitHub Actions y el proxy suelen ser «US»)."""
    m = re.search(r'"countryCode":"([A-Z]{2})"', html)
    return m.group(1) if m else None

def amazon_read(html, asin=None):
    """(precio, error, origen) a partir de la ficha. Fuera de España se suma el IVA y se marca el origen."""
    country = amazon_country(html) or "?"
    spain = country == "ES"
    price = amazon_buybox(html)
    if price is None and asin:
        time.sleep(1.5)
        try:
            price = amazon_offers(asin)
        except requests.RequestException:
            price = None
    if price is None:
        return None, NO_DISPONIBLE if spain else f"no visible desde fuera de España ({country})", country
    if country not in EU:
        price = round(price * IVA, 2)  # Amazon.es enseña precios sin IVA a quien visita desde fuera de la UE
    return price, None, country

def amazon_direct(url, asin):
    r = http_get(url, timeout=20)
    low = r.text.lower()
    if r.status_code != 200 or "captcha" in low or "robot check" in low:
        return None, "bloqueado", None, None
    return (*amazon_read(r.text, asin), r.text)

def amazon_proxy(url):
    """Vía r.jina.ai, para cuando Amazon bloquea la petición directa."""
    for intento in range(2):
        try:
            r = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "html"}, timeout=60)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200 and len(r.text) > 50000:
            return (*amazon_read(r.text), r.text)
        time.sleep(5 * (intento + 1))
    return None, "proxy sin respuesta", None, None

def fetch_amazon(url, asin):
    """Devuelve (precio, error, fuente, origen, html)."""
    try:
        price, err, origen, html = amazon_direct(url, asin)
        if err != "bloqueado":
            return price, err, "directo", origen, html
    except requests.RequestException:
        pass
    price, err, origen, html = amazon_proxy(url)
    return price, err, "proxy", origen, html

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
    m = re.search(r'(?:schema\.org/|"availability"\s*:\s*")(InStock|LimitedAvailability|OutOfStock|SoldOut|Discontinued|BackOrder|PreOrder)', html)
    return m.group(1) in ("InStock", "LimitedAvailability") if m else None  # BackOrder = "en más de 15 días" (LDLC)

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
        r = http_get(url, timeout=25)
    except requests.RequestException as e:
        return store_proxy(url) or (None, f"red: {type(e).__name__}", None)
    blocked = is_blocked(r)
    if blocked == "protegido (Cloudflare)":  # el proxy tampoco pasa el reto de Cloudflare
        return None, blocked, None
    if blocked:
        return store_proxy(url) or (None, blocked, None)
    if r.status_code in (404, 410):
        return None, f"enlace roto o producto retirado (HTTP {r.status_code})", None
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

def notify(title, body, click=None, tags="moneybag", priority=None):
    print(f"AVISO [{title}]: {body}")
    if not TOPIC:
        return
    headers = {"Title": title, "Tags": tags}
    if click:
        headers["Click"] = click
    if priority:
        headers["Priority"] = priority
    for intento in range(2):  # un aviso perdido es peor que uno repetido: se reintenta una vez
        try:
            requests.post(f"https://ntfy.sh/{TOPIC}", data=body.encode("utf-8"), headers=headers, timeout=15)
            return
        except (requests.RequestException, UnicodeError) as e:
            print("No se pudo enviar el aviso:", e)
            time.sleep(3)

def enviar_avisos(avisos):
    """Pocos avisos: uno a uno (cada uno abre su tienda). Muchos (p. ej. una rebaja general): uno solo con la lista."""
    if len(avisos) <= MAX_AVISOS:
        for a in avisos:
            notify(**a)
        return
    lineas, usado = [], 0
    for a in avisos:
        linea = "• " + a["body"]
        usado += len(linea.encode("utf-8")) + 1
        if usado > 3500:  # ntfy corta los mensajes de más de 4 KB
            lineas.append(f"… y {len(avisos) - len(lineas)} más (mira la página)")
            break
        lineas.append(linea)
    urgente = any(a.get("priority") == "high" for a in avisos)
    notify(f"{len(avisos)} novedades en tus precios", "\n".join(lineas), PAGINA, "bell", "high" if urgente else None)

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

# ---------- Configuración ----------

def validar_config(conf):
    """(errores, avisos) de products.json. Los errores impiden la pasada; los avisos solo se muestran."""
    errores, avisos = [], []
    productos = conf.get("productos")
    if not isinstance(productos, list) or not productos:
        return ["products.json no tiene la lista «productos»"], avisos
    try:
        if float(conf.get("ajustes", {}).get("bajada_minima_pct", 1)) < 0:
            errores.append("«bajada_minima_pct» no puede ser negativo")
    except (TypeError, ValueError):
        errores.append("«bajada_minima_pct» debe ser un número")
    envio, vistos, sin_envio = conf.get("envio", {}), set(), set()
    for i, p in enumerate(productos, 1):
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            errores.append(f"producto nº {i}: falta «id» o tiene caracteres no válidos (solo letras, cifras, - y _)")
            continue
        if pid in vistos:
            errores.append(f"id repetido: {pid}")
        vistos.add(pid)
        if not p.get("nombre"):
            errores.append(f"[{pid}] falta «nombre»")
        tiendas = p.get("tiendas")
        if not isinstance(tiendas, dict):
            errores.append(f"[{pid}] falta «tiendas»")
            continue
        obj = p.get("objetivo")
        if obj is not None and (isinstance(obj, bool) or not isinstance(obj, (int, float)) or obj <= 0):
            errores.append(f"[{pid}] «objetivo» debe ser un número mayor que 0 (o null)")
        for store, url in tiendas.items():
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            dom = DOMINIOS.get(store)
            if not host:
                errores.append(f"[{pid}/{store}] el enlace no es válido: {url}")
            elif dom and host != dom and not host.endswith("." + dom):
                avisos.append(f"[{pid}/{store}] el enlace no es de {dom} sino de {host}")
            if store not in envio:
                sin_envio.add(store)
        if tiendas.get("amazon") and not asin_de(p):
            avisos.append(f"[{pid}] no se encuentra el ASIN: sin él no se leen las ofertas ocultas de Amazon")
    for store in sorted(sin_envio):
        avisos.append(f"«{store}» no tiene datos en «envio»: los totales lo contarán con envío gratis")
    return errores, avisos

# ---------- Pasada ----------

CASA = os.getenv("MODO") == "casa"  # ejecución en el PC de casa (IP española): solo Amazon, en su propio archivo
AMAZON_ES = ROOT / "data" / "amazon_es.json"
AMAZON_ES_JS = ROOT / "data" / "amazon_es.js"
FRESCO_CASA = 12 * 3600  # si casa leyó Amazon hace menos de esto, GitHub no lo vuelve a leer

def _edad(ts):
    try:
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")

def casa_vale(r, max_edad=FRESCO_CASA):
    """¿Sirve una lectura de Amazon hecha desde casa? Solo si es reciente y dio un precio o un «no disponible»
    fiable; un captcha o un fallo de red no cuentan (ni deben tapar el precio que leyó GitHub)."""
    return bool(r) and _edad(r.get("comprobado")) < max_edad and (r.get("precio") is not None or r.get("error") == NO_DISPONIBLE)

def leer_tienda(p, store, url, rec):
    """Lee una tienda; devuelve (precio, error) y anota en rec la fuente, el origen y el stock."""
    if store == "amazon" and CASA:
        try:
            price, err, origen, html = amazon_direct(url, asin_de(p))
        except requests.RequestException as e:
            price, err, origen, html = None, f"red: {type(e).__name__}", None, None
        if price is not None and origen != "ES":  # p. ej. con VPN: no es una lectura desde España
            price, err = None, f"no leído desde España ({origen})"
        rec["fuente"], rec["_origen"] = "casa", "ES"
    elif store == "amazon":
        price, err, fuente, origen, html = fetch_amazon(url, asin_de(p))
        rec["fuente"], rec["_origen"] = fuente, "ES" if origen == "ES" else "fuera"
    else:
        price, err, stock = fetch_store(url)
        if stock is not None:  # si no se pudo leer, se conserva el último estado conocido
            # la vuelta de stock queda anotada hasta que se avise, aunque el precio no se valide en esta pasada
            rec["restock"] = stock is True and (rec.get("restock") is True or rec.get("stock") is False)
            rec["stock"] = stock
        return price, err
    if html:
        save_image(p["id"], amazon_image(html))
    return price, err

def actualizar(p, store, rec, price, otros, now, min_drop, avisos):
    """Valida el precio leído, lo guarda en rec y apunta los avisos que toquen."""
    nombre, tienda = p["nombre"], NOMBRES.get(store, store)
    if price is not None:
        if rec.get("stock") is False:
            print(f"[{p['id']}/{store}] sin stock")
        why = suspicious(price, otros, rec)
        if why:
            rec["error"], price = why, None
    if price is None:
        if rec.get("precio") is not None:
            rec["ultimo"], rec["ultimo_fecha"] = rec["precio"], rec.get("fecha_precio", now)
        rec["precio"] = None
        rec.pop("_origen", None)
        print(f"[{p['id']}/{store}] sin precio: {rec['error']}")
        return

    rec.pop("pendiente", None)
    if rec.pop("restock", False):
        avisos.append(dict(title="Vuelve a haber stock", body=f"{nombre}: disponible en {tienda} a {price:.2f} €",
                           click=rec["url"], tags="package"))
    old = rec.get("precio") if rec.get("precio") is not None else rec.get("ultimo")
    # Amazon visto desde España y desde fuera (GitHub) da ofertas distintas: solo se comparan lecturas del mismo origen
    origen, prev_origen = rec.pop("_origen", None), rec.get("origen")
    comparable = origen is None or prev_origen is None or origen == prev_origen
    if origen:
        rec["origen"] = origen
    hist = rec["historial"]
    minimo = len(hist) >= 3 and price < min(v for _, v in hist)  # por debajo de todo lo visto hasta ahora
    rec.update(precio=price, fecha_precio=now)
    rec.pop("ultimo", None)
    rec.pop("ultimo_fecha", None)
    if old != price:
        rec["historial"] = (hist + [[now, price]])[-200:]
    print(f"[{p['id']}/{store}] {price:.2f} €")

    target = p.get("objetivo")
    dropped = old is not None and price <= old * (1 - min_drop / 100)
    hit_target = bool(target) and price <= target and (old is None or old > target)
    if (dropped or hit_target) and comparable and rec.get("stock") is not False:
        motivo = []
        if old is not None and price < old:
            motivo.append(f"antes {old:.2f} €, −{(old - price) / old * 100:.1f} %")
        if hit_target:
            motivo.append(f"objetivo {target:.2f} € alcanzado")
        if minimo:
            motivo.append("mínimo histórico")
        avisos.append(dict(title="Objetivo alcanzado" if hit_target else "Bajada de precio",
                           body=f"{nombre}: {price:.2f} € en {tienda} ({'; '.join(motivo)})", click=rec["url"],
                           tags="dart" if hit_target else "chart_with_downwards_trend", priority="high" if hit_target else None))

def main():
    conf = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    errores, avisos_conf = validar_config(conf)
    for a in avisos_conf:
        print(f"::warning::products.json: {a}")
    if errores:
        for e in errores:
            print(f"::error::products.json: {e}")
        sys.exit(1)
    if not TOPIC:
        print("::warning::NTFY_TOPIC está vacío: no se enviarán avisos al móvil")
    elif os.getenv("PRUEBA_AVISO") == "true":
        notify("Prueba del vigilante", "Si ves esto, los avisos de bajada de precio te llegarán al móvil.",
               PAGINA, "white_check_mark")
    min_drop = float(conf.get("ajustes", {}).get("bajada_minima_pct", 1))
    state_file = AMAZON_ES if CASA else STATE
    state = cargar_json(state_file)
    # Lecturas de la otra parte: en casa, los precios de las tiendas (para validar); en GitHub, el Amazon de casa
    otro = cargar_json(STATE if CASA else AMAZON_ES, estricto=False)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    first = True
    avisos = []     # se envían al final, ya guardado el estado: si la pasada falla, no se repiten
    leidas = {}     # tienda -> [lecturas con precio, intentos]

    for p in conf["productos"]:
        item = state.setdefault(p["id"], {})
        tiendas = {"amazon": p["tiendas"].get("amazon")} if CASA else p["tiendas"]
        for store in list(item):
            if not tiendas.get(store):
                del item[store]
        found = {}
        for store, url in tiendas.items():
            if not url:
                continue
            if store == "amazon" and not CASA and casa_vale(otro.get(p["id"], {}).get("amazon")):
                continue  # casa lo ha leído hace poco desde España; la lectura desde fuera vale menos
            if not first:
                time.sleep(DELAY * random.uniform(0.7, 1.3))
            first = False

            rec = item.setdefault(store, {})
            if rec.get("url") != url:  # enlace nuevo o cambiado: su historial ya no vale
                rec.clear()
            rec.setdefault("historial", [])
            try:
                price, err = leer_tienda(p, store, url, rec)
            except Exception as e:  # un fallo inesperado en una tienda no debe tirar toda la pasada
                price, err = None, f"fallo interno: {type(e).__name__}"
                print(f"[{p['id']}/{store}] {err}: {e}")
            rec.update(url=url, comprobado=now, error=err)
            found[store] = price

        # Para validar se usan también las lecturas que esta pasada no hizo: en casa, las demás tiendas (leídas por
        # GitHub); en GitHub, el Amazon de casa. Las que no tienen stock se ignoran: sus precios suelen ser de relleno.
        if CASA:
            ajenos = {s: r.get("precio") for s, r in otro.get(p["id"], {}).items() if s != "amazon" and r.get("stock") is not False}
        else:
            r = otro.get(p["id"], {}).get("amazon") or {}
            ajenos = {"amazon": r["precio"]} if "amazon" not in found and casa_vale(r, 24 * 3600) and r.get("precio") else {}
        # Validar y guardar después de leer todas las tiendas, para poder comparar entre ellas
        for store, price in found.items():
            otros = [v for s, v in {**ajenos, **{s: v for s, v in found.items() if item[s].get("stock") is not False}}.items()
                     if s != store and v is not None]
            actualizar(p, store, item[store], price, otros, now, min_drop, avisos)
            n = leidas.setdefault(store, [0, 0])
            n[0] += price is not None  # cuenta la lectura, no que se acepte (un cambio brusco general queda pendiente)
            n[1] += 1

    escribir_atomico(state_file, json.dumps(state, ensure_ascii=False, indent=1))
    if CASA:
        payload = {"actualizado": now, "precios": state}
        escribir_atomico(AMAZON_ES_JS, "window.AMAZON_ES = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    else:
        payload = {"actualizado": now, "productos": conf["productos"], "envio": conf.get("envio", {}), "precios": state}
        escribir_atomico(OUT_JS, "window.DATOS = " + json.dumps(payload, ensure_ascii=False) + ";\n")

    enviar_avisos(avisos)
    for store, (ok, n) in sorted(leidas.items()):
        print(f"Resumen {NOMBRES.get(store, store)}: {ok}/{n} lecturas con precio")
    if leidas and not any(ok for ok, _ in leidas.values()):
        sys.exit("::error::No se ha podido leer ningún precio en esta pasada (¿sin conexión o IP bloqueada?)")

if __name__ == "__main__":
    main()
