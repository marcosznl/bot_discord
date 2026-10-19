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
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$RAIZ/logs/servico.log</string>
    <key>StandardErrorPath</key><string>$RAIZ/logs/servico.log</string>
</dict>
</plist>
PLIST
    launchctl bootout "$DOMINIO/$ROTULO" 2>/dev/null || true
    launchctl bootstrap "$DOMINIO" "$PLIST"
    echo "Servico instalado e rodando. Logs: $RAIZ/logs/"
    ;;
remover)
    launchctl bootout "$DOMINIO/$ROTULO" 2>/dev/null || true
    rm -f "$PLIST"
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
