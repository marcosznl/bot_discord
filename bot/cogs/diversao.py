import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot.config import GIPHY_API_KEY


SEM_CHAVE_GIPHY = "Os gifs estao desligados: falta a `GIPHY_API_KEY` no `.env` do bot."


async def _buscar_gif(busca: str) -> str | None:
    if not GIPHY_API_KEY:
        return None
    params = {"api_key": GIPHY_API_KEY, "q": busca, "limit": 25, "rating": "pg-13"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get("https://api.giphy.com/v1/gifs/search", params=params) as resp:
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        return None

    resultados = data.get("data")
    if not resultados:
        return None
    return random.choice(resultados)["images"]["original"]["url"]


# chave -> (nome mostrado no Discord, busca no Giphy, frase da mensagem).
# O Discord aceita no maximo 25 opcoes numa lista - esta cheia.
INTERACOES = {
    "abraco": ("Abracar", "anime hug", "{autor} deu um abraco em {alvo}"),
    "abraco_apertado": ("Abraco apertado", "anime tight hug", "{autor} deu um abraco bem apertado em {alvo}"),
    "beijo": ("Beijar", "anime kiss", "{autor} deu um beijo em {alvo}"),
    "beijo_bochecha": ("Beijo na bochecha", "anime cheek kiss", "{autor} deu um beijo na bochecha de {alvo}"),
    "cafune": ("Fazer cafune", "anime head pat", "{autor} fez cafune em {alvo}"),
    "carinho": ("Fazer carinho", "anime cuddle", "{autor} fez carinho em {alvo}"),
    "segurar_mao": ("Segurar a mao", "anime holding hands", "{autor} segurou a mao de {alvo}"),
    "charme": ("Jogar charme", "anime flirt", "{autor} jogou um charme para {alvo}"),
    "piscar": ("Piscar", "anime wink", "{autor} piscou para {alvo}"),
    "acenar": ("Acenar", "anime wave", "{autor} acenou para {alvo}"),
    "toca_aqui": ("Toca aqui", "anime high five", "{autor} bateu um toca aqui com {alvo}"),
    "dancar": ("Dancar junto", "anime dance together", "{autor} chamou {alvo} pra dancar"),
    "comemorar": ("Comemorar", "anime celebrate", "{autor} comemorou junto com {alvo}"),
    "alimentar": ("Dar comida", "anime feed", "{autor} deu comida na boca de {alvo}"),
    "consolar": ("Consolar", "anime comfort", "{autor} consolou {alvo}"),
    "cutucar": ("Cutucar", "anime poke", "{autor} cutucou {alvo}"),
    "cocegas": ("Fazer cocegas", "anime tickle", "{autor} fez cocegas em {alvo}"),
    "morder": ("Morder", "anime bite", "{autor} mordeu {alvo}"),
    "encarar": ("Encarar", "anime stare", "{autor} ficou encarando {alvo}"),
    "rir": ("Rir da cara", "anime laugh at", "{autor} riu da cara de {alvo}"),
    "chorar": ("Chorar no ombro", "anime cry", "{autor} chorou no ombro de {alvo}"),
    "tapa": ("Dar um tapa", "anime slap", "{autor} deu um tapa em {alvo}"),
    "socao": ("Dar um socao", "anime punch", "{autor} deu um socao em {alvo}"),
    "chute": ("Dar um chute", "anime kick", "{autor} deu um chute em {alvo}"),
    "travesseiro": ("Guerra de travesseiro", "anime pillow fight", "{autor} jogou um travesseiro em {alvo}"),
}


class Diversao(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gif", description="Busca um gif pela palavra-chave")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.checks.cooldown(3, 30, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(busca="Palavra-chave para buscar o gif")
    async def gif(self, interaction: discord.Interaction, busca: str):
        if not GIPHY_API_KEY:
            await interaction.response.send_message(SEM_CHAVE_GIPHY, ephemeral=True)
            return
        await interaction.response.defer()

        gif_url = await _buscar_gif(busca)
        if gif_url is None:
            await interaction.followup.send(f'Nenhum gif encontrado para "{busca}".')
            return

        await interaction.followup.send(gif_url)

    @app_commands.command(name="interagir", description="Interage com alguem usando um gif de anime")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(3, 30, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(acao="O que fazer", pessoa="Com quem")
    @app_commands.choices(
        acao=[app_commands.Choice(name=nome, value=chave) for chave, (nome, _, _) in INTERACOES.items()]
    )
    async def interagir(self, interaction: discord.Interaction, acao: app_commands.Choice[str], pessoa: discord.Member):
        await _enviar_interacao(interaction, acao.value, interaction.user, pessoa)


async def _enviar_interacao(
    interaction: discord.Interaction, acao: str, autor: discord.abc.User, alvo: discord.abc.User
) -> None:
    _, busca, frase = INTERACOES[acao]
    if not GIPHY_API_KEY:
        enviar = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await enviar(SEM_CHAVE_GIPHY, ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer()

    gif_url = await _buscar_gif(busca)
    if gif_url is None:
        await interaction.followup.send("Nao encontrei nenhum gif para isso agora.")
        return

    embed = discord.Embed()
    embed.set_image(url=gif_url)
    view = None
    if alvo.id != autor.id and not alvo.bot:
        view = discord.ui.View(timeout=None)
        view.add_item(BotaoRetribuir(acao, autor.id, alvo.id))
    await interaction.followup.send(
        content=frase.format(autor=autor.mention, alvo=alvo.mention), embed=embed, view=view
    )


class BotaoRetribuir(discord.ui.DynamicItem[discord.ui.Button], template=r"interagir:(?P<acao>\w+):(?P<autor>\d+):(?P<alvo>\d+)"):
    """Botao "Retribuir" embaixo de cada /interagir - so quem recebeu pode
    clicar, e devolve a mesma acao para quem mandou. Todo o estado mora no
    custom_id, entao continua funcionando depois que o bot reinicia."""

    def __init__(self, acao: str, autor_id: int, alvo_id: int):
        super().__init__(
            discord.ui.Button(
                label="Retribuir",
                style=discord.ButtonStyle.secondary,
                custom_id=f"interagir:{acao}:{autor_id}:{alvo_id}",
            )
        )
        self.acao = acao
        self.autor_id = autor_id
        self.alvo_id = alvo_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["acao"], int(match["autor"]), int(match["alvo"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.acao not in INTERACOES:
            await interaction.response.send_message("Essa interacao nao existe mais.", ephemeral=True)
            return
        if interaction.user.id != self.alvo_id:
            await interaction.response.send_message("So quem recebeu pode retribuir.", ephemeral=True)
            return
        # Desativa o botao para nao retribuir duas vezes a mesma interacao (e
        # isso ja conta como a resposta dentro da janela de 3s).
        self.item.disabled = True
        self.view.clear_items().add_item(self)
        await interaction.response.edit_message(view=self.view)

        autor_original = interaction.guild.get_member(self.autor_id) if interaction.guild else None
        if autor_original is None:
            autor_original = await interaction.client.fetch_user(self.autor_id)
        await _enviar_interacao(interaction, self.acao, interaction.user, autor_original)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoRetribuir)
    await bot.add_cog(Diversao(bot))
