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
Luego abre `index.html` con doble clic: verás los precios, la imagen de cada componente (se guardan en `img/`) y un gráfico con la evolución del precio en cada tienda, además del total de la mejor combinación. Pasa el ratón (o toca) sobre un gráfico para ver los precios de cada momento.

Desde tu ordenador (IP española) Amazon da el precio exacto con IVA. Si ejecutas el vigilante en local, sube después los cambios (`git add data img && git commit -m "Actualizar precios" && git push`).

## Ajustes

- `bajada_minima_pct` (en `products.json`): porcentaje mínimo de bajada para avisar. Por defecto, 1 %.
- `objetivo` (en cada producto): precio al que quieres que te avise.
- Frecuencia: línea `cron` de `.github/workflows/precios.yml`.

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
