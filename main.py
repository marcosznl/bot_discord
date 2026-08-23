import asyncio
import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import llm as llm_cog
from bot.config import STATUS_SEM_SERVIDOR, TOKEN

logger = logging.getLogger(__name__)

BOT_INICIO = time.time()

intents = discord.Intents.default()
intents.members = True  # necessario para o evento de boas-vindas (on_member_join)
intents.message_content = True  # necessario para ler o texto de mensagens normais (ponte de chat do MC)

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


class TentarComandoNovamente(discord.ui.View):
    """Mostrado quando a interacao original expira antes do bot conseguir
    responder (o Discord so espera 3s pela primeira resposta). Ao clicar,
    reexecuta o mesmo comando com os mesmos parametros, mas numa interacao
    nova - com uma janela de 3s do zero."""

    def __init__(self, comando: app_commands.Command, kwargs: dict, autor_id: int):
        super().__init__(timeout=300)
        self.comando = comando
        self.kwargs = kwargs
        self.autor_id = autor_id
        self.mensagem: discord.Message | None = None

    @discord.ui.button(label="Tentar de novo", style=discord.ButtonStyle.primary)
    async def tentar(self, interaction: discord.Interaction, botao: discord.ui.Button) -> None:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "So quem pediu o comando pode tentar de novo.", ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True
        if self.mensagem is not None:
            await self.mensagem.edit(view=self)
        self.stop()

        if self.comando.binding is not None:
            await self.comando._callback(self.comando.binding, interaction, **self.kwargs)
        else:
            await self.comando._callback(interaction, **self.kwargs)


@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    comando = interaction.command
    # interaction.response.is_done() so fica True depois que a PRIMEIRA
    # resposta e aceita pelo Discord (o codigo interno do discord.py so seta
    # isso apos a chamada de rede ter sucesso). Se ainda for False aqui, a
    # interacao nunca foi respondida nem uma vez - ou seja, nao teve como o
    # comando ter passado da primeira linha (todo comando deste bot responde
    # ou da defer antes de qualquer efeito colateral). So nesse caso e seguro
    # reexecutar do zero sem risco de duplicar uma acao que ja rodou.
    interacao_nunca_respondida = not interaction.response.is_done()
    interacao_expirou = (
        isinstance(error, app_commands.CommandInvokeError)
        and isinstance(error.original, discord.NotFound)
        and error.original.code == 10062
        and interacao_nunca_respondida
    )

    if interacao_expirou and comando is not None and interaction.channel is not None:
        kwargs = {nome: valor for nome, valor in interaction.namespace}
        view = TentarComandoNovamente(comando, kwargs, interaction.user.id)
        view.mensagem = await interaction.channel.send(
            f"{interaction.user.mention} a interacao expirou antes de eu conseguir responder ao "
            f"`/{comando.qualified_name}` (o Discord so espera 3s pela primeira resposta).",
            view=view,
        )
        return

    if comando is not None:
        if comando._has_any_error_handlers():
            return
        logger.error(f"Ignorando excecao no comando {comando.name!r}", exc_info=error)
    else:
        logger.error("Ignorando excecao na arvore de comandos", exc_info=error)


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
    logger.info(f"Bot conectado como {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN, root_logger=True)
