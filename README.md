# Vigilante de precios

Vigila los precios de tus componentes, te avisa al móvil cuando bajan y los muestra en `index.html`.

## Puesta en marcha

1. **Móvil:** instala la app **ntfy** (Android / iOS) y suscríbete a un canal con un nombre largo y difícil de adivinar, por ejemplo `nombre-pc-x7k29qd`. Quien conozca el nombre puede mandarte avisos, así que no lo compartas.
2. **Repositorio:** crea un repositorio **público** en GitHub y sube todo el contenido de esta carpeta (incluida `.github`). Es público para poder usar GitHub Pages gratis.
3. **Secreto:** en el repositorio, *Settings → Secrets and variables → Actions → New repository secret*. Nombre: `NTFY_TOPIC`. Valor: el nombre del canal.
4. **Enlaces:** en `products.json` pega, para cada producto, el enlace de la ficha en cada tienda (`pccomponentes`, `neobyte`, `coolmod`, `pcbox`). Las tiendas con `""` se ignoran. Puedes poner un precio en `objetivo` para que te avise al llegar a él.
5. **Primera ejecución:** pestaña *Actions → Vigilar precios → Run workflow*. Después se ejecuta solo cada 6 horas.
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

- **Amazon.es:** primero entra directamente a la ficha y lee el precio de la caja de compra; si Amazon la oculta, usa la oferta nueva más barata de «Ver todas las opciones de compra». Si Amazon bloquea la petición (pasa a menudo desde GitHub), lo lee a través del proxy `r.jina.ai`. El proxy entra desde fuera de la UE, donde Amazon enseña precios sin IVA, así que el vigilante les suma el 21 %. Los productos que Amazon no envía fuera de la UE aparecen como «sin oferta visible desde el proxy» en esas pasadas.
- **Coolmod y PcBox:** leen bien el precio y si hay stock. Los productos sin stock salen tachados y no cuentan para el más barato ni para los totales.
- **PcComponentes y Neobyte:** están protegidas con Cloudflare y bloquean cualquier lectura automática. Sus enlaces sirven para abrir la ficha y comparar a mano.

## Protecciones contra precios erróneos

- Si una tienda da un precio más de 2,5 veces mayor o menor que la mediana de las demás, se descarta como «precio sospechoso».
- Si un precio cambia más de un 60 % de golpe, no se acepta hasta que la siguiente comprobación lo confirme.
- Cuando una tienda falla, la página muestra el último precio conocido en gris, con su fecha, en vez de hacerlo pasar por el actual.
