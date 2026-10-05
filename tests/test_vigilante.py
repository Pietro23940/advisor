"""Pruebas del vigilante: funciones puras y pasadas completas con las tiendas simuladas (sin red)."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vigilante as v

PRODUCTOS = [
    {"id": "cpu", "nombre": "CPU de prueba", "asin": "B000000001", "objetivo": None,
     "tiendas": {"amazon": "https://www.amazon.es/dp/B000000001", "coolmod": "https://www.coolmod.com/cpu/",
                 "neobyte": "https://www.neobyte.es/cpu.html", "pcbox": "https://www.pcbox.com/cpu/p"}},
]


class Pasada(unittest.TestCase):
    """Ejecuta main() en una carpeta temporal con las lecturas de tiendas simuladas."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "data").mkdir()
        ajustes = dict(ROOT=self.root, STATE=self.root / "data" / "precios.json", OUT_JS=self.root / "data" / "precios.js",
                       AMAZON_ES=self.root / "data" / "amazon_es.json", AMAZON_ES_JS=self.root / "data" / "amazon_es.js",
                       IMG_DIR=self.root / "img", TOPIC="canal-de-prueba", CASA=False)
        for k, val in ajustes.items():
            p = mock.patch.object(v, k, val)
            p.start()
            self.addCleanup(p.stop)
        for nombre, fn in (("time.sleep", None), ("save_image", None)):
            obj, attr = (v.time, "sleep") if nombre == "time.sleep" else (v, nombre)
            p = mock.patch.object(obj, attr, fn or (lambda *a, **k: None))
            p.start()
            self.addCleanup(p.stop)
        self.posts = []
        p = mock.patch.object(v.requests, "post", side_effect=lambda url, data=None, headers=None, **k: self.posts.append(
            (data.decode() if isinstance(data, bytes) else data, headers or {})))
        p.start()
        self.addCleanup(p.stop)
        self.tiendas = {}   # tienda -> (precio, error, stock)
        self.amazon = (None, "no visible desde fuera de España (US)", "proxy", "US", None)
        self.set_conf(PRODUCTOS)

    def set_conf(self, productos, **extra):
        envio = {t: {"coste": 4, "gratis_desde": 50} for t in v.DOMINIOS}
        conf = {"ajustes": {"bajada_minima_pct": 1}, "envio": envio, "productos": productos, **extra}
        (self.root / "products.json").write_text(json.dumps(conf), encoding="utf-8")

    def correr(self, **tiendas):
        """tienda=(precio, stock); None = fallo de lectura."""
        self.tiendas = {s: ((val[0], None, val[1]) if val else (None, "bloqueado (403)", None)) for s, val in tiendas.items()}
        self.posts.clear()
        with mock.patch.object(v, "fetch_store", lambda url: next(r for s, r in self.tiendas.items() if s in url)), \
                mock.patch.object(v, "fetch_amazon", lambda url, asin: self.amazon):
            v.main()
        return json.loads((self.root / "data" / "precios.json").read_text(encoding="utf-8"))

    def titulos(self):
        return [h["Title"] for _, h in self.posts]


class TestPasadaBase(Pasada):
    def test_primera_lectura_guarda_precios_sin_avisar(self):
        s = self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(s["cpu"]["coolmod"]["precio"], 100.0)
        self.assertEqual(s["cpu"]["coolmod"]["historial"][0][1], 100.0)
        self.assertEqual(self.posts, [])
        js = (self.root / "data" / "precios.js").read_text(encoding="utf-8")
        self.assertTrue(js.startswith("window.DATOS = "))
        datos = json.loads(js[len("window.DATOS = "):].rstrip().rstrip(";"))
        self.assertEqual(datos["productos"][0]["id"], "cpu")

    def test_bajada_avisa_una_vez(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        s = self.correr(coolmod=(90.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.titulos(), ["Bajada de precio"])
        self.assertIn("90.00", self.posts[0][0])
        self.assertEqual([p[1] for p in s["cpu"]["coolmod"]["historial"]], [100.0, 90.0])
        self.correr(coolmod=(90.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.posts, [])

    def test_subida_no_avisa(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(103.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.posts, [])

    def test_objetivo_alcanzado(self):
        self.set_conf([{**PRODUCTOS[0], "objetivo": 95}])
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(94.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(len(self.posts), 1)
        self.assertIn("objetivo", self.posts[0][0])

    def test_precio_sospechoso_se_descarta_y_conserva_el_ultimo(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        s = self.correr(coolmod=(10.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        rec = s["cpu"]["coolmod"]
        self.assertIsNone(rec["precio"])
        self.assertEqual(rec["ultimo"], 100.0)
        self.assertIn("sospechoso", rec["error"])
        self.assertEqual(self.posts, [])

    def test_cambio_brusco_se_confirma_a_la_segunda(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        s = self.correr(coolmod=(30.0, True), neobyte=(35.0, True), pcbox=(36.0, True))
        self.assertIsNone(s["cpu"]["coolmod"]["precio"])
        s = self.correr(coolmod=(30.0, True), neobyte=(35.0, True), pcbox=(36.0, True))
        self.assertEqual(s["cpu"]["coolmod"]["precio"], 30.0)

    def test_vuelta_de_stock_avisa(self):
        self.correr(coolmod=(100.0, False), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.posts, [])
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.titulos(), ["Vuelve a haber stock"])
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.posts, [])

    def test_sin_stock_no_avisa_de_bajada(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(90.0, False), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.posts, [])

    def test_fallo_conserva_ultimo_precio_conocido(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        s = self.correr(coolmod=None, neobyte=(105.0, True), pcbox=(110.0, True))
        rec = s["cpu"]["coolmod"]
        self.assertIsNone(rec["precio"])
        self.assertEqual(rec["ultimo"], 100.0)
        self.assertEqual(rec["error"], "bloqueado (403)")

    def test_enlace_cambiado_reinicia_historial(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        prod = json.loads(json.dumps(PRODUCTOS))
        prod[0]["tiendas"]["coolmod"] = "https://www.coolmod.com/otra-cpu/"
        self.set_conf(prod)
        s = self.correr(coolmod=(80.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual([p[1] for p in s["cpu"]["coolmod"]["historial"]], [80.0])
        self.assertEqual(self.posts, [])

    def test_amazon_desde_fuera_se_marca_y_no_compara_con_origen_distinto(self):
        self.amazon = (121.0, None, "proxy", "US", None)
        s = self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(s["cpu"]["amazon"]["origen"], "fuera")
        self.amazon = (60.0, None, "proxy", "ES", None)   # lectura española: no es una «bajada»
        s = self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(s["cpu"]["amazon"]["origen"], "ES")
        self.assertEqual(self.posts, [])


HACE_1H = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(timespec="minutes")
HACE_3D = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat(timespec="minutes")


class TestPasadaRobustez(Pasada):
    def test_un_fallo_inesperado_en_una_tienda_no_tira_la_pasada(self):
        def lee(url):
            if "coolmod" in url:
                raise AttributeError("la web cambió")
            return (105.0, None, True)
        with mock.patch.object(v, "fetch_store", lee), mock.patch.object(v, "fetch_amazon", lambda *a: self.amazon):
            v.main()
        s = json.loads((self.root / "data" / "precios.json").read_text(encoding="utf-8"))
        self.assertIn("fallo interno", s["cpu"]["coolmod"]["error"])
        self.assertIsNone(s["cpu"]["coolmod"]["precio"])
        self.assertEqual(s["cpu"]["neobyte"]["precio"], 105.0)

    def test_los_avisos_se_envian_despues_de_guardar_el_estado(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        guardado = []   # precio que ya estaba en disco en el momento de enviar el aviso

        def al_enviar(url, data=None, headers=None, **k):
            guardado.append(json.loads((self.root / "data" / "precios.json").read_text(encoding="utf-8"))["cpu"]["coolmod"]["precio"])
        with mock.patch.object(v.requests, "post", side_effect=al_enviar):
            self.correr(coolmod=(90.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(guardado, [90.0])

    def test_muchos_avisos_se_agrupan_en_uno(self):
        prod = [{**PRODUCTOS[0], "id": f"p{i}", "nombre": f"Pieza {i}"} for i in range(6)]
        self.set_conf(prod)
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(90.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.titulos(), ["6 novedades en tus precios"])
        cuerpo, cab = self.posts[0]
        self.assertEqual(cuerpo.count("•"), 6)
        self.assertEqual(cab["Click"], v.PAGINA)

    def test_pocos_avisos_van_sueltos_con_su_enlace(self):
        prod = [{**PRODUCTOS[0], "id": f"p{i}", "nombre": f"Pieza {i}"} for i in range(v.MAX_AVISOS)]
        self.set_conf(prod)
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(90.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(len(self.posts), v.MAX_AVISOS)
        self.assertTrue(all(h["Click"].startswith("https://www.coolmod.com") for _, h in self.posts))

    def test_aviso_de_objetivo_es_prioritario_y_menciona_minimo_historico(self):
        self.set_conf([{**PRODUCTOS[0], "objetivo": 80}])
        for precio in (100.0, 98.0, 96.0, 94.0):
            self.correr(coolmod=(precio, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(79.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        cuerpo, cab = self.posts[0]
        self.assertEqual(cab["Title"], "Objetivo alcanzado")
        self.assertEqual(cab["Priority"], "high")
        self.assertIn("mínimo histórico", cuerpo)
        self.assertIn("Coolmod", cuerpo)

    def test_vuelta_de_stock_no_se_pierde_si_el_precio_no_se_valida(self):
        self.correr(coolmod=(100.0, False), neobyte=(105.0, True), pcbox=(110.0, True))
        self.correr(coolmod=(5.0, True), neobyte=(105.0, True), pcbox=(110.0, True))   # precio sospechoso
        self.assertEqual(self.posts, [])
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(self.titulos(), ["Vuelve a haber stock"])

    def test_precios_sin_stock_no_cuentan_para_la_mediana(self):
        # con stock: 100 y 105; el precio de relleno de la tercera tienda (sin stock) no debe hacer sospechoso al resto
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(900.0, False))
        s = self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(900.0, False))
        self.assertEqual(s["cpu"]["coolmod"]["precio"], 100.0)

    def test_sin_ninguna_lectura_termina_con_error_pero_guarda_el_estado(self):
        with self.assertRaises(SystemExit):
            self.correr(coolmod=None, neobyte=None, pcbox=None)
        s = json.loads((self.root / "data" / "precios.json").read_text(encoding="utf-8"))
        self.assertEqual(s["cpu"]["coolmod"]["error"], "bloqueado (403)")

    def test_estado_corrupto_aborta_sin_pisarlo(self):
        f = self.root / "data" / "precios.json"
        f.write_text("{no es json", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(f.read_text(encoding="utf-8"), "{no es json")

    def test_no_quedan_archivos_temporales(self):
        self.correr(coolmod=(100.0, True), neobyte=(105.0, True), pcbox=(110.0, True))
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_config_invalida_aborta_antes_de_leer(self):
        self.set_conf([PRODUCTOS[0], PRODUCTOS[0]])
        with self.assertRaises(SystemExit), mock.patch.object(v, "fetch_store", side_effect=AssertionError("no debía leer")):
            v.main()


class TestAmazonCasa(Pasada):
    def casa(self, **rec):
        base = {"historial": [], "url": PRODUCTOS[0]["tiendas"]["amazon"], "origen": "ES", "fuente": "casa", **rec}
        (self.root / "data" / "amazon_es.json").write_text(json.dumps({"cpu": {"amazon": base}}), encoding="utf-8")

    def lecturas_amazon(self):
        llamadas = []
        self.amazon = (121.0, None, "proxy", "US", None)
        orig = lambda url, asin: (llamadas.append(url), self.amazon)[1]
        with mock.patch.object(v, "fetch_store", lambda url: (105.0, None, True)), mock.patch.object(v, "fetch_amazon", orig):
            v.main()
        return llamadas

    def test_github_no_relee_amazon_si_casa_acaba_de_leerlo(self):
        self.casa(precio=300.0, comprobado=HACE_1H, error=None)
        self.assertEqual(self.lecturas_amazon(), [])

    def test_github_relee_amazon_si_la_lectura_de_casa_fue_un_fallo(self):
        self.casa(precio=None, comprobado=HACE_1H, error="bloqueado")
        self.assertEqual(len(self.lecturas_amazon()), 1)

    def test_github_confia_en_un_no_disponible_de_casa(self):
        self.casa(precio=None, comprobado=HACE_1H, error=v.NO_DISPONIBLE)
        self.assertEqual(self.lecturas_amazon(), [])

    def test_github_relee_amazon_si_la_lectura_de_casa_es_vieja(self):
        self.casa(precio=300.0, comprobado=HACE_3D, error=None)
        self.assertEqual(len(self.lecturas_amazon()), 1)

    def test_el_amazon_de_casa_ayuda_a_validar_el_resto(self):
        # Amazon (casa) 100 y Neobyte 105 dejan en evidencia a Coolmod con 500 aunque GitHub no haya leído Amazon
        self.casa(precio=100.0, comprobado=HACE_1H, error=None)
        with mock.patch.object(v, "fetch_store", lambda url: (500.0 if "coolmod" in url else 105.0, None, True)):
            v.main()
        s = json.loads((self.root / "data" / "precios.json").read_text(encoding="utf-8"))
        self.assertIn("sospechoso", s["cpu"]["coolmod"]["error"])

    def test_modo_casa_solo_lee_amazon_y_escribe_su_archivo(self):
        (self.root / "data" / "precios.json").write_text(json.dumps(
            {"cpu": {"coolmod": {"precio": 100.0}, "neobyte": {"precio": 105.0}}}), encoding="utf-8")
        with mock.patch.object(v, "CASA", True), \
                mock.patch.object(v, "amazon_direct", lambda url, asin: (99.0, None, "ES", None)), \
                mock.patch.object(v, "fetch_store", side_effect=AssertionError("casa solo lee Amazon")):
            v.main()
        s = json.loads((self.root / "data" / "amazon_es.json").read_text(encoding="utf-8"))
        self.assertEqual(s["cpu"]["amazon"]["precio"], 99.0)
        self.assertEqual(s["cpu"]["amazon"]["fuente"], "casa")
        self.assertTrue((self.root / "data" / "amazon_es.js").read_text(encoding="utf-8").startswith("window.AMAZON_ES = "))

    def test_modo_casa_descarta_lecturas_que_no_son_de_espana(self):
        with mock.patch.object(v, "CASA", True), mock.patch.object(v, "amazon_direct", lambda url, asin: (99.0, None, "US", None)):
            with self.assertRaises(SystemExit):   # única lectura descartada: no hay ningún precio
                v.main()
        s = json.loads((self.root / "data" / "amazon_es.json").read_text(encoding="utf-8"))
        self.assertIsNone(s["cpu"]["amazon"]["precio"])
        self.assertIn("no leído desde España", s["cpu"]["amazon"]["error"])


class Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {}


class TestFunciones(unittest.TestCase):
    def test_parse_price(self):
        casos = {"1.299,99 €": 1299.99, "299,00": 299.0, "299.00": 299.0, "1,299.50": 1299.5, "1.299": 1299.0,
                 "1 299,00 €": 1299.0, "9,94": 9.94, "EUR 59.96": 59.96, "": None, "gratis": None, None: None,
                 "0,10": None, "25000": None, 349.9: 349.9}
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(v.parse_price(entrada), esperado)

    def test_extract_price_meta_microdatos_y_jsonld(self):
        self.assertEqual(v.extract_price('<meta property="product:price:amount" content="339,89">'), 339.89)
        self.assertEqual(v.extract_price('<span itemprop="price" content="12.5">'), 12.5)
        ld = '<script type="application/ld+json">{"@graph":[{"@type":"Product","offers":[{"@type":"Offer","price":"41.75"}]}]}</script>'
        self.assertEqual(v.extract_price(ld), 41.75)
        self.assertIsNone(v.extract_price('<script type="application/ld+json">{no json</script><p>nada</p>'))

    def test_in_stock(self):
        self.assertTrue(v.in_stock('<meta property="product:availability" content="in stock">'))
        self.assertFalse(v.in_stock('<meta property="product:availability" content="out of stock">'))
        self.assertTrue(v.in_stock('"availability": "https://schema.org/InStock"'))
        self.assertFalse(v.in_stock('"availability":"OutOfStock"'))
        self.assertFalse(v.in_stock('"availability":"BackOrder"'))
        self.assertIsNone(v.in_stock("<html></html>"))

    def test_amazon_buybox_ignora_precio_tachado(self):
        html = ('<div id="corePriceDisplay_desktop_feature_div"><span class="a-price a-text-price"><span class="a-offscreen">399,00 €</span></span>'
                '<span class="priceToPay"><span class="a-offscreen">309,99 €</span></span></div>')
        self.assertEqual(v.amazon_buybox(html), 309.99)
        self.assertIsNone(v.amazon_buybox("<html></html>"))

    def test_amazon_country_y_asin(self):
        self.assertEqual(v.amazon_country('..."countryCode":"US"...'), "US")
        self.assertIsNone(v.amazon_country("nada"))
        self.assertEqual(v.asin_de({"asin": "B000000001"}), "B000000001")
        self.assertEqual(v.asin_de({"tiendas": {"amazon": "https://www.amazon.es/Nombre-largo/dp/B09NSTR7JZ/ref=x"}}), "B09NSTR7JZ")
        self.assertIsNone(v.asin_de({"tiendas": {"amazon": ""}}))

    def test_suspicious(self):
        self.assertIn("sospechoso", v.suspicious(10, [100, 105], {}))
        self.assertIn("sospechoso", v.suspicious(900, [100, 105], {}))
        self.assertIsNone(v.suspicious(10, [100], {}))            # con una sola referencia no se juzga
        rec = {"precio": 100.0}
        self.assertIn("cambio brusco", v.suspicious(30, [], rec))
        self.assertEqual(rec["pendiente"], 30)
        self.assertIsNone(v.suspicious(30, [], rec))              # la segunda lectura lo confirma

    def test_http_get_reintenta_errores_pasajeros(self):
        sess = mock.Mock()
        sess.get.side_effect = [v.requests.ConnectionError(), Resp(502), Resp(200, "ok")]
        with mock.patch.object(v.time, "sleep"):
            r = v.http_get("https://x", sess=sess, tries=3)
        self.assertEqual(r.text, "ok")
        self.assertEqual(sess.get.call_count, 3)

    def test_http_get_no_reintenta_bloqueos_y_propaga_el_ultimo_error(self):
        sess = mock.Mock()
        sess.get.return_value = Resp(403)
        self.assertEqual(v.http_get("https://x", sess=sess).status_code, 403)
        self.assertEqual(sess.get.call_count, 1)
        sess.get.side_effect = v.requests.Timeout()
        with mock.patch.object(v.time, "sleep"), self.assertRaises(v.requests.Timeout):
            v.http_get("https://x", sess=sess, tries=2)

    def test_fetch_store_distingue_enlace_roto(self):
        with mock.patch.object(v, "http_get", return_value=Resp(404, "")):
            self.assertIn("enlace roto", v.fetch_store("https://x")[1])

    def test_fetch_store_lee_precio_y_stock(self):
        html = '<meta property="product:price:amount" content="99,90"><meta property="product:availability" content="in stock">'
        with mock.patch.object(v, "http_get", return_value=Resp(200, html)):
            self.assertEqual(v.fetch_store("https://x"), (99.9, None, True))

    def test_casa_vale(self):
        self.assertTrue(v.casa_vale({"precio": 10.0, "comprobado": HACE_1H}))
        self.assertTrue(v.casa_vale({"precio": None, "error": v.NO_DISPONIBLE, "comprobado": HACE_1H}))
        self.assertFalse(v.casa_vale({"precio": None, "error": "bloqueado", "comprobado": HACE_1H}))
        self.assertFalse(v.casa_vale({"precio": 10.0, "comprobado": HACE_3D}))
        self.assertFalse(v.casa_vale({}))
        self.assertFalse(v.casa_vale(None))

    def test_enviar_avisos_corta_el_resumen_para_no_pasar_de_4_kb(self):
        avisos = [dict(title="t", body="x" * 200) for _ in range(40)]
        with mock.patch.object(v, "notify") as n:
            v.enviar_avisos(avisos)
        cuerpo = n.call_args.args[1]
        self.assertLess(len(cuerpo.encode()), 4096)
        self.assertIn("y ", cuerpo.splitlines()[-1])

    def test_notify_no_revienta_con_cabeceras_no_latin1(self):
        with mock.patch.object(v, "TOPIC", "canal"), mock.patch.object(v.time, "sleep"), \
                mock.patch.object(v.requests, "post", side_effect=UnicodeEncodeError("latin-1", "€", 0, 1, "x")) as post:
            v.notify("Título €", "cuerpo")
        self.assertEqual(post.call_count, 2)


class TestValidarConfig(unittest.TestCase):
    def prod(self, **kw):
        return {"id": "cpu", "nombre": "CPU", "tiendas": {"amazon": "https://www.amazon.es/dp/B000000001"}, **kw}

    def validar(self, *productos, **extra):
        return v.validar_config({"envio": {"amazon": {}, "coolmod": {}}, "productos": list(productos), **extra})

    def test_products_json_real_es_valido(self):
        conf = json.loads((Path(__file__).resolve().parent.parent / "products.json").read_text(encoding="utf-8"))
        errores, avisos = v.validar_config(conf)
        self.assertEqual((errores, avisos), ([], []))

    def test_errores(self):
        casos = {
            "sin productos": ({"productos": []}, "productos"),
            "id repetido": ({"productos": [self.prod(), self.prod()]}, "repetido"),
            "id con espacios": ({"productos": [self.prod(id="mi cpu")]}, "id"),
            "sin nombre": ({"productos": [self.prod(nombre="")]}, "nombre"),
            "objetivo texto": ({"productos": [self.prod(objetivo="barato")]}, "objetivo"),
            "objetivo cero": ({"productos": [self.prod(objetivo=0)]}, "objetivo"),
            "enlace roto": ({"productos": [self.prod(tiendas={"amazon": "no-es-un-enlace"})]}, "enlace"),
            "bajada mala": ({"ajustes": {"bajada_minima_pct": "x"}, "productos": [self.prod()]}, "bajada_minima_pct"),
        }
        for nombre, (conf, texto) in casos.items():
            with self.subTest(nombre):
                errores, _ = v.validar_config(conf)
                self.assertTrue(any(texto in e for e in errores), errores)

    def test_avisos(self):
        _, avisos = self.validar(self.prod(tiendas={"amazon": "https://www.coolmod.com/algo"}))
        self.assertTrue(any("no es de amazon.es" in a for a in avisos), avisos)
        _, avisos = self.validar(self.prod(tiendas={"neobyte": "https://www.neobyte.es/x.html"}))
        self.assertTrue(any("envio" in a for a in avisos), avisos)
        _, avisos = self.validar(self.prod(tiendas={"amazon": "https://www.amazon.es/"}))
        self.assertTrue(any("ASIN" in a for a in avisos), avisos)


if __name__ == "__main__":
    unittest.main()
