"""Emojis proprios do bot (emojis de aplicacao): ficam no bot, nao ocupam
espaco de emoji dos servidores e funcionam em qualquer um. Os PNGs vem de
assets/emojis/ (gerados por scripts/gerar_imagens.sh) e sao enviados ao
Discord na primeira vez que o bot liga."""

import logging
import os

import discord

from bot.config import BASE_DIR

logger = logging.getLogger(__name__)

PASTA = os.path.join(BASE_DIR, "assets", "emojis")
# Emoji comum usado enquanto o proprio ainda nao foi enviado (ou se falhar).
RESERVA = {
    "tocar": "▶️",
    "pular": "⏭️",
    "anterior": "⏮️",
    "parar": "⏹️",
    "fila": "📜",
    "atualizar": "🔄",
    "sorteio": "🎁",
    "verificado": "✅",
}
PREFIXO = "arco_"

_carregados: dict[str, discord.Emoji] = {}


def emoji(nome: str) -> discord.Emoji | str:
    return _carregados.get(nome) or RESERVA[nome]


async def sincronizar(client: discord.Client) -> None:
    """Envia os emojis que faltam e guarda todos para uso nos paineis."""
    try:
        existentes = {e.name: e for e in await client.fetch_application_emojis()}
    except discord.HTTPException as erro:
        logger.warning(f"Nao consegui listar os emojis do bot: {erro}")
        return
    for nome in RESERVA:
        nome_discord = PREFIXO + nome
        if nome_discord in existentes:
            _carregados[nome] = existentes[nome_discord]
            continue
        caminho = os.path.join(PASTA, f"{nome}.png")
        if not os.path.exists(caminho):
            continue
        with open(caminho, "rb") as arquivo:
            imagem = arquivo.read()
        try:
            _carregados[nome] = await client.create_application_emoji(name=nome_discord, image=imagem)
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui enviar o emoji {nome_discord}: {erro}")
