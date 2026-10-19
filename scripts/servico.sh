#!/bin/bash
# Mantem o bot sempre ligado no Mac via launchd: liga junto com o login e
# reinicia sozinho se cair.
#   scripts/servico.sh instalar | remover | reiniciar | status | logs
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ROTULO="com.marcosznl.botdiscord"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
DOMINIO="gui/$(id -u)"

case "${1:-}" in
instalar)
    [ -x "$RAIZ/venv/bin/python" ] || { echo "Crie o venv antes (veja o README)."; exit 1; }
    [ -f "$RAIZ/.env" ] || { echo "Crie o .env com o DISCORD_TOKEN antes."; exit 1; }
    mkdir -p "$RAIZ/logs" "$(dirname "$PLIST")"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$ROTULO</string>
    <key>ProgramArguments</key>
    <array>
        <string>$RAIZ/venv/bin/python</string>
        <string>$RAIZ/main.py</string>
    </array>
    <key>WorkingDirectory</key><string>$RAIZ</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
    <key>ProcessType</key><string>Interactive</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$RAIZ/logs/servico.log</string>
    <key>StandardErrorPath</key><string>$RAIZ/logs/servico.log</string>
</dict>
</plist>
PLIST
    # O Python da Homebrew roda como um app (Python.app); em segundo plano e
    # na bateria o macOS o coloca em App Nap, que atrasa rede e timers - e o
    # Discord so espera 3s pela resposta de um comando.
    defaults write org.python.python NSAppSleepDisabled -bool YES
    # Reinstalando: o bootout volta antes do servico antigo terminar de
    # sair, e o bootstrap falha (erro 5) se rodar cedo demais.
    launchctl bootout "$DOMINIO/$ROTULO" 2>/dev/null || true
    for _ in $(seq 1 20); do
        launchctl print "$DOMINIO/$ROTULO" >/dev/null 2>&1 || break
        sleep 0.5
    done
    launchctl bootstrap "$DOMINIO" "$PLIST"
    echo "Servico instalado e rodando. Logs: $RAIZ/logs/"
    ;;
remover)
    launchctl bootout "$DOMINIO/$ROTULO" 2>/dev/null || true
    rm -f "$PLIST"
    defaults delete org.python.python NSAppSleepDisabled 2>/dev/null || true
    echo "Servico removido."
    ;;
reiniciar)
    launchctl kickstart -k "$DOMINIO/$ROTULO"
    echo "Bot reiniciado."
    ;;
status)
    launchctl print "$DOMINIO/$ROTULO" 2>/dev/null | grep -E "^\s*(state|pid|last exit code)" || echo "Servico nao instalado."
    ;;
logs)
    tail -n 50 -f "$RAIZ/logs/bot.log"
    ;;
*)
    echo "Uso: $0 instalar | remover | reiniciar | status | logs"
    exit 1
    ;;
esac
