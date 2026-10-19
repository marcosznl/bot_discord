import discord
from discord import app_commands
from discord.ext import commands

from bot import layout

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando;
# para grupos, "mod" lista todos os subcomandos de /mod).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "ajuda", "usuario", "avatar", "servidor"],
    "Moderacao": ["mod", "logs", "automod", "boasvindas"],
    "Comunidade": ["enquete", "cargos", "tickets", "evento", "lembrete"],
    "Niveis": ["xp"],
    "Diversao": ["interagir", "gif"],
    "Musica": ["musica"],
}


def _linhas_comando(comando: app_commands.Command | app_commands.Group) -> list[str]:
    if isinstance(comando, app_commands.Group):
        return [f"`/{sub.qualified_name}` - {sub.description}" for sub in comando.commands]
    return [f"`/{comando.name}` - {comando.description}"]


def _menus_de_contexto(bot: commands.Bot) -> dict[discord.AppCommandType, list[str]]:
    menus: dict[discord.AppCommandType, list[str]] = {}
    for tipo in (discord.AppCommandType.user, discord.AppCommandType.message):
        menus[tipo] = [menu.name for menu in bot.tree.get_commands(type=tipo)]
    return menus


def painel_ajuda(bot: commands.Bot) -> discord.ui.LayoutView:
    comandos_por_nome = {comando.name: comando for comando in bot.tree.get_commands()}

    container = discord.ui.Container(
        layout.texto("## Comandos disponiveis\n-# Digite `/` no chat para ver todos com os parametros."),
        accent_color=discord.Color.blue(),
    )
    for categoria, nomes in COMANDOS_CATEGORIAS.items():
        linhas = []
        for nome in nomes:
            comando = comandos_por_nome.get(nome)
            if comando:
                linhas += _linhas_comando(comando)
        if linhas:
            container.add_item(layout.separador())
            container.add_item(layout.texto(f"**{categoria}**\n" + "\n".join(linhas)))

    menus = _menus_de_contexto(bot)
    linhas_menus = []
    if menus[discord.AppCommandType.user]:
        linhas_menus.append("Em uma pessoa: " + ", ".join(menus[discord.AppCommandType.user]))
    if menus[discord.AppCommandType.message]:
        linhas_menus.append("Em uma mensagem: " + ", ".join(menus[discord.AppCommandType.message]))
    if linhas_menus:
        container.add_item(layout.separador())
        container.add_item(
            layout.texto("**Clique direito > Apps**\n" + "\n".join(linhas_menus))
        )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


class Geral(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Testa se o bot esta online e mostra a latencia")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Pong! {round(self.bot.latency * 1000)} ms")

    @app_commands.command(name="ajuda", description="Mostra todos os comandos do bot, por categoria")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def ajuda(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=painel_ajuda(self.bot), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Geral(bot))
