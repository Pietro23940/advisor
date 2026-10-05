# Vigilante de precios

Vigila los precios de tus componentes, te avisa al móvil cuando bajan y los muestra en `index.html`.

## Puesta en marcha

1. **Móvil:** instala la app **ntfy** (Android / iOS) y suscríbete a un canal con un nombre largo y difícil de adivinar, por ejemplo `nombre-pc-x7k29qd`. Quien conozca el nombre puede mandarte avisos, así que no lo compartas.
2. **Repositorio:** crea un repositorio **público** en GitHub y sube todo el contenido de esta carpeta (incluida `.github`). Es público para poder usar GitHub Pages gratis.
3. **Secreto:** en el repositorio, *Settings → Secrets and variables → Actions → New repository secret*. Nombre: `NTFY_TOPIC`. Valor: el nombre del canal.
4. **Enlaces:** en `products.json` pega, para cada producto, el enlace de la ficha en cada tienda (`pccomponentes`, `neobyte`, `coolmod`, `pcbox`). Las tiendas con `""` se ignoran. Puedes poner un precio en `objetivo` para que te avise al llegar a él.
5. **Primera ejecución:** pestaña *Actions → Vigilar precios → Run workflow*. Después se ejecuta solo cada 3 horas.
6. **Página:** *Settings → Pages → Branch: main, carpeta / (root)*. Tu página quedará en `https://TU_USUARIO.github.io/NOMBRE_REPO/`.

## Probar en tu ordenador

```
pip install requests
NTFY_TOPIC=tu-canal python vigilante.py
```
Luego abre `index.html` con doble clic. La página es deliberadamente sencilla y responde a una pregunta: **cuánto me cuesta comprarlo todo hoy**.

- **Arriba, el total** de la combinación más barata, con los envíos incluidos.
- **Qué comprar y dónde**: las piezas agrupadas por tienda, cada una con su subtotal. Pulsa una pieza para ver su precio en todas las tiendas (la diferencia con la elegida, el enlace «Abrir» y por qué no se compra en la más barata: envío o falta de stock) y un gráfico con su evolución. Pasa el ratón (o toca) sobre el gráfico para ver el precio de cada momento.
- **Cambios recientes**: las subidas y bajadas de las últimas 48 horas.
- **★ Mínimo histórico** y **✓ Objetivo** junto a las piezas que lo cumplen, y cuánto falta para tu `objetivo`.
- «Si lo compraras todo en una sola tienda», plegado, para comparar con el total.
- Un **aviso amarillo** si los datos llevan más de 8 horas sin actualizarse (señal de que el vigilante está parado), y el gráfico de evolución del total solo si ha variado de forma apreciable.

Las imágenes de `img/` ya no se muestran en la página (el vigilante las sigue guardando por si se quieren recuperar).

Para ejecutar las pruebas: `python -m unittest discover -s tests`.

Desde tu ordenador (IP española) Amazon da el precio exacto con IVA. Si ejecutas el vigilante en local, sube después los cambios (`git add data img && git commit -m "Actualizar precios" && git push`).

## Ajustes

- `bajada_minima_pct` (en `products.json`): porcentaje mínimo de bajada para avisar. Por defecto, 1 %.
- `objetivo` (en cada producto): precio al que quieres que te avise.
- Frecuencia: línea `cron` de `.github/workflows/precios.yml`.
- Variables de entorno: `DELAY` (segundos entre peticiones, 4 por defecto) y `PAGINA_URL` (enlace que abren los avisos resumidos).
- En `products.json` solo hace falta el enlace de Amazon: el `asin` se saca de él si no lo pones.

## Avisos

Te llega un aviso cuando:

- **Baja el precio** en una tienda (más de `bajada_minima_pct`). Dice cuánto ha bajado y si es **mínimo histórico**.
- **Se alcanza tu `objetivo`** (aviso de prioridad alta).
- **Vuelve a haber stock** de una pieza.

Si en una misma pasada hay más de 4 avisos (p. ej. una rebaja general) se reciben juntos, en uno solo con la lista. Los avisos se envían cuando ya se han guardado los precios, así que si una pasada falla no se repiten.

## Cómo lee cada tienda

- **Amazon.es:** lee el precio de la caja de compra y, si Amazon la oculta, la oferta nueva más barata de «Ver todas las opciones de compra». Si Amazon bloquea la petición, usa el proxy `r.jina.ai`.
  - Desde GitHub Actions (servidores en EE. UU.), Amazon enseña precios sin IVA y oculta lo que no envía fuera de la UE. El vigilante lo detecta (`countryCode`), suma el 21 % y en la página aparece como «estimado, leído desde fuera de España». Los productos ocultos conservan su último precio conocido.
  - Solo se avisa de una bajada si se compara con un precio leído desde el mismo sitio (España o fuera); así no saltan avisos falsos al alternar entre tu ordenador y GitHub.
  - Para tener todos los precios exactos de Amazon, ejecuta de vez en cuando el vigilante en tu ordenador y sube los datos.
- **Coolmod, PcBox y Neobyte:** leen precio y stock (si una tienda bloquea, se reintenta vía proxy). Los productos sin stock salen tachados, no cuentan para el más barato ni los totales, y te llega un aviso cuando vuelven a estar disponibles.
- **PcComponentes:** casi siempre bloquea con Cloudflare; su enlace sirve para comparar a mano.

## Protecciones contra precios erróneos

- Si una tienda da un precio más de 2,5 veces mayor o menor que la mediana de las demás, se descarta como «precio sospechoso».
- Si un precio cambia más de un 60 % de golpe, no se acepta hasta que la siguiente comprobación lo confirme.
- Cuando una tienda falla, la página muestra el último precio conocido en gris, con su fecha, en vez de hacerlo pasar por el actual.
- Los precios de tiendas sin stock no cuentan para decidir si el de otra es sospechoso (suelen ser de relleno).
- Una lectura de Amazon hecha en casa solo sustituye a la de GitHub si dio un precio o un «no disponible» fiable; un captcha o un fallo de red en casa no tapa el precio bueno.

## Robustez

- `products.json` se valida al empezar (ids repetidos, objetivos no numéricos, enlaces que no son de la tienda indicada...). Los errores paran la pasada y se muestran en el log de Actions; los avisos no.
- Un fallo inesperado en una tienda no interrumpe a las demás: queda anotado como «fallo interno» en esa tienda.
- Los errores de red y los 500/502/504 pasajeros se reintentan. Un 404 se muestra como «enlace roto o producto retirado».
- Los archivos de `data/` se escriben enteros o nada. Si `precios.json` estuviera corrupto, el vigilante se detiene en vez de empezar de cero y perder el historial.
- Si en una pasada no se lee ningún precio (sin conexión, IP bloqueada...) el workflow termina en rojo, y GitHub te avisa por correo. Los fallos quedan guardados igualmente.
- `casa.sh` no se ejecuta dos veces a la vez.
