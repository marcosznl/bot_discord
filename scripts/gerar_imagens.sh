#!/bin/bash
# Gera as imagens do projeto a partir dos HTML em design/ usando o Chrome
# sem janela: icone e capa do README (docs/) e os emojis proprios do bot
# (assets/emojis/, enviados ao Discord na primeira vez que o bot liga).
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

renderizar() {  # html  saida  largura  altura
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
        --default-background-color=00000000 --window-size="$3,$4" \
        --screenshot="$2" "file://$RAIZ/design/$1" >/dev/null 2>&1
    echo "gerado: ${2#$RAIZ/}"
}

for nome in tocar pular anterior parar fila atualizar sorteio verificado; do
    renderizar "emoji.html?nome=$nome" "$RAIZ/assets/emojis/$nome.png" 128 128
done
renderizar "icone.html" "$RAIZ/docs/icone.png" 512 512
renderizar "avatar.html" "$RAIZ/docs/avatar.png" 1024 1024
renderizar "capa.html" "$RAIZ/docs/previa-social.png" 1280 640
