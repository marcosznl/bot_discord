import logging
import os
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import commands

from bot import emojis, layout
from bot.config import BASE_DIR, TOKEN
from bot.traducao import TradutorIngles
from bot.retry_ui import interacao_expirou, oferecer_tentar_de_novo

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.members = True  # boas-vindas, logs de entrada/saida, XP em call, aniversarios
# Conteudo das mensagens - para o canal de logs mostrar o que foi apagado ou
# editado, o jogo de contagem e o mural de destaques. Precisa estar ligado em Developer Portal > Bot > Privileged
# Gateway Intents, senao o bot nem conecta.
intents.message_content = True

EXTENSOES = [
    "bot.cogs.boasvindas",
    "bot.cogs.diversao",
    "bot.cogs.musica",
    "bot.cogs.xp",
    "bot.cogs.moderacao",
    "bot.cogs.info",
    "bot.cogs.comunidade",
    "bot.cogs.sorteios",
    "bot.cogs.sugestoes",
    "bot.cogs.verificacao",
    "bot.cogs.candidaturas",
    "bot.cogs.voz",
    "bot.cogs.aniversarios",
    "bot.cogs.destaques",
    "bot.cogs.contagem",
    "bot.cogs.expressoes",
    "bot.cogs.anuncios",
    "bot.cogs.servidor",
    "bot.cogs.manutencao",
    "bot.cogs.configuracao",
    "bot.cogs.geral",
]


def intents_faltando(flags: discord.ApplicationFlags) -> list[str]:
    """Intents privilegiados que o bot usa mas que estao desligados no
    Developer Portal."""
    faltando = []
    if not (flags.gateway_guild_members or flags.gateway_guild_members_limited):
        faltando.append("Server Members Intent")
    if not (flags.gateway_message_content or flags.gateway_message_content_limited):
        faltando.append("Message Content Intent")
    return faltando


class ArcoBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Sem os intents ligados no portal, o Discord derruba a conexao com
        # 4014 e o discord.py as vezes fica tentando reconectar em loop.
        # Conferir antes deixa o erro claro e para o bot na hora.
        faltando = intents_faltando((await self.application_info()).flags)
        if faltando:
            logger.error(f"Ligue no Developer Portal > Bot > Privileged Gateway Intents (e salve): {', '.join(faltando)}")
            raise discord.PrivilegedIntentsRequired(None)

        await self.tree.set_translator(TradutorIngles())
        await emojis.sincronizar(self)
        self.add_dynamic_items(layout.BotaoFixo)
        for extensao in EXTENSOES:
            await self.load_extension(extensao)


bot = ArcoBot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    activity=discord.Game(name="Dattebayo!"),
    # Nada aqui usa guild.members ou cache de membros - so o intent e
    # necessario (pros eventos de membro). Sem isso, toda reconexao baixava
    # a lista inteira de membros de cada servidor a toa.
    chunk_guilds_at_startup=False,
)
tree = bot.tree

# on_ready dispara em toda reconexao de gateway, nao so na primeira - essa
# flag evita ressincronizar a arvore de comandos (bulk_upsert, sujeito a rate
# limit) toda vez que a rede pisca. So false de novo se a propria sync falhar.
_arvore_sincronizada = False


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    # O bot so usa slash commands - quando alguem menciona o bot, o discord.py
    # tenta interpretar isso como prefixo de comando de texto e gera esse erro
    # a toa. Ignorado de proposito.
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


def _reexecutar(comando: app_commands.Command, kwargs: dict):
    async def retomar(interaction: discord.Interaction) -> None:
        if comando.binding is not None:
            await comando._callback(comando.binding, interaction, **kwargs)
        else:
            await comando._callback(interaction, **kwargs)

    return retomar


@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    comando = interaction.command
    original = getattr(error, "original", error)

    # Todo comando deste bot responde ou da defer antes de qualquer efeito
    # colateral, entao se a interacao expirou sem resposta nenhuma da pra
    # reexecutar do zero com os mesmos parametros. Menus de contexto ficam de
    # fora: o alvo deles nao vem no namespace.
    if (
        isinstance(comando, app_commands.Command)
        and isinstance(error, app_commands.CommandInvokeError)
        and interacao_expirou(original, interaction)
    ):
        kwargs = {nome: valor for nome, valor in interaction.namespace}
        await oferecer_tentar_de_novo(
            interaction, _reexecutar(comando, kwargs), f" ao `/{comando.qualified_name}`"
        )
        return

    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Calma! Tente de novo em {error.retry_after:.0f}s.", ephemeral=True
        )
        return

    # Sem permissao no servidor (cargo do bot abaixo do alvo, canal trancado
    # pro bot etc.) - avisa em vez de so sumir com o erro no log.
    if isinstance(original, discord.Forbidden):
        texto = "Nao tenho permissao no Discord para fazer isso aqui (confira os cargos e permissoes do bot)."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(texto, ephemeral=True)
            else:
                await interaction.response.send_message(texto, ephemeral=True)
        except discord.HTTPException:
            pass
        return

    if comando is not None:
        if isinstance(comando, app_commands.Command) and comando._has_any_error_handlers():
            return
        logger.error(f"Ignorando excecao no comando {comando.name!r}", exc_info=error)
    else:
        logger.error("Ignorando excecao na arvore de comandos", exc_info=error)


@bot.listen()
async def on_interaction(interaction: discord.Interaction) -> None:
    # O Discord so espera 3s pela primeira resposta. Se as interacoes
    # chegarem atrasadas, o problema e a rede/maquina, nao o comando.
    atraso = (discord.utils.utcnow() - interaction.created_at).total_seconds()
    if atraso > 1.5:
        logger.warning(f"Interacao chegou com {atraso:.1f}s de atraso (o limite do Discord e 3s)")


@bot.event
async def on_ready():
    if bot.owner_id is None:
        app_info = await bot.application_info()
        bot.owner_id = app_info.owner.id

    global _arvore_sincronizada
    if not _arvore_sincronizada:
        await tree.sync()
        _arvore_sincronizada = True

    logger.info(f"Bot conectado como {bot.user}")


def configurar_logs() -> None:
    """Console colorido (padrao do discord.py) + arquivo em logs/bot.log que
    gira em 5 arquivos de 5 MB, para investigar erros depois."""
    discord.utils.setup_logging(root=True)
    pasta = os.path.join(BASE_DIR, "logs")
    os.makedirs(pasta, exist_ok=True)
    arquivo = RotatingFileHandler(
        os.path.join(pasta, "bot.log"), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.getLogger().addHandler(arquivo)


if __name__ == "__main__":
    configurar_logs()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        logger.error(
            "O Discord recusou os intents do bot. Ligue 'Server Members Intent' e "
            "'Message Content Intent' em https://discord.com/developers/applications > Bot."
        )
