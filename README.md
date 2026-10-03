# Vigilante de precios

Vigila los precios de tus componentes, te avisa al móvil cuando bajan y los muestra en `index.html`.

## Puesta en marcha

1. **Móvil:** instala la app **ntfy** (Android / iOS) y suscríbete a un canal con un nombre largo y difícil de adivinar, por ejemplo `pietro-pc-x7k29qd`. Quien conozca el nombre puede mandarte avisos, así que no lo compartas.
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
Luego abre `index.html` con doble clic. Es la mejor forma de ver qué tiendas te dejan leer el precio.

## Ajustes

- `bajada_minima_pct` (en `products.json`): porcentaje mínimo de bajada para avisar. Por defecto, 1 %.
- Frecuencia: línea `cron` de `.github/workflows/precios.yml`.

## Limitaciones

- Amazon y algunas tiendas bloquean las peticiones desde GitHub. Cuando pase, la página mostrará «bloqueado» en esa casilla y no avisará de ese producto. Para Amazon.es es más fiable usar Keepa.
- El script lee el precio del código de la ficha (datos estructurados de la tienda). Si una tienda cambia su web, puede dejar de encontrarlo («precio no encontrado»).
