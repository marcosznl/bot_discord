import asyncio
import json
import time

import discord
from discord.ext import commands, tasks

from bot.cogs import llm as llm_cog
from bot.config import STATUS_SEM_SERVIDOR, TOKEN

BOT_INICIO = time.time()

intents = discord.Intents.default()
intents.members = True  # necessario para o evento de boas-vindas (on_member_join)

# Cogs de feature, carregados um a um conforme vao sendo extraidos de main.py.
EXTENSOES = [
    "bot.cogs.boasvindas",
    "bot.cogs.diversao",
    "bot.cogs.musica",
    "bot.cogs.playlist",
    "bot.cogs.xp",
    "bot.cogs.minecraft",
    "bot.cogs.llm",
    "bot.cogs.geral",
    "bot.cogs.admin",
]


class ArcoBot(commands.Bot):
    async def setup_hook(self) -> None:
        for extensao in EXTENSOES:
            await self.load_extension(extensao)


bot = ArcoBot(command_prefix=commands.when_mentioned, intents=intents)
tree = bot.tree


def _formatar_duracao(segundos: int) -> str:
    horas, segundos_restantes = divmod(segundos, 3600)
    minutos, _ = divmod(segundos_restantes, 60)
    if horas:
        return f"{horas}h{minutos}m"
    return f"{minutos}m"


def _bot_tempo_ligado() -> str:
    return _formatar_duracao(int(time.time() - BOT_INICIO))


async def _change_presence_multipla(atividades: list) -> None:
    payload = {
        "op": bot.ws.PRESENCE,
        "d": {
            "activities": [a.to_dict() for a in atividades],
            "afk": False,
            "since": 0,
            "status": "online",
        },
    }
    await bot.ws.send(json.dumps(payload))


@tasks.loop(seconds=60)
async def atualizar_presence():
    atividade_jogo = discord.Game(name=f"{STATUS_SEM_SERVIDOR} {_bot_tempo_ligado()}")
    atividade_custom = discord.CustomActivity(name=atividade_jogo.name)
    await _change_presence_multipla([atividade_jogo, atividade_custom])


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    # O bot so usa slash commands - quando alguem menciona o bot (ex: "@bot oi"
    # para falar com a LLM), o discord.py tenta interpretar isso como prefixo
    # de comando de texto e gera esse erro a toa. Ignorado de proposito.
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


@bot.event
async def on_ready():
    if not atualizar_presence.is_running():
        atualizar_presence.start()
    cog_musica = bot.get_cog("Musica")
    if not cog_musica.verificar_ocioso_voz.is_running():
        cog_musica.verificar_ocioso_voz.start()
    cog_mc = bot.get_cog("Minecraft")
    if not cog_mc.monitorar_chat_minecraft.is_running():
        cog_mc.monitorar_chat_minecraft.start()
    if not cog_mc.verificar_mc_ocioso.is_running():
        cog_mc.verificar_mc_ocioso.start()
    cog_llm = bot.get_cog("LLM")
    if not cog_llm.verificar_llm_ociosa.is_running():
        cog_llm.verificar_llm_ociosa.start()
    cog_xp = bot.get_cog("XP")
    if not cog_xp.verificar_xp_semana.is_running():
        cog_xp.verificar_xp_semana.start()

    if llm_cog._llm_ultima_interacao is None and await asyncio.to_thread(llm_cog._llm_servidor_rodando):
        # A LLM ja estava ligada antes deste (re)inicio do bot (ex: restart durante
        # desenvolvimento) - conta a partir de agora, ja que nao ha como saber
        # de fato desde quando ela esta sem uso.
        llm_cog._llm_ultima_interacao = time.time()

    app_info = await bot.application_info()
    bot.owner_id = app_info.owner.id
    await tree.sync()
    print(f"Bot conectado como {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
