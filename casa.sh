#!/bin/bash
# Lee Amazon desde este PC (IP española: precios con IVA y todas las ofertas) y sube el resultado a GitHub.
# Lo lanza el temporizador de systemd «vigilante-amazon.timer»; también se puede ejecutar a mano.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# Esperar a tener conexión (p. ej. justo después de encender el PC)
for _ in $(seq 1 12); do
    curl -s -m 5 -o /dev/null https://www.amazon.es && break
    sleep 10
done

git pull -q --rebase --autostash origin main
MODO=casa python3 vigilante.py
git add data/amazon_es.json data/amazon_es.js img
git diff --cached --quiet && exit 0
git commit -q -m "Actualizar precios de Amazon (desde casa)"
for _ in 1 2 3; do
    git pull -q --rebase --autostash origin main && git push -q origin main && exit 0
    sleep 20
done
echo "No se pudo subir a GitHub" >&2
exit 1
