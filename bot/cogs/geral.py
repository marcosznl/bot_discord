import discord
from discord import app_commands
from discord.ext import commands

from bot import layout
from bot.retry_ui import LayoutViewComRetry

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando;
# grupos como "mod" listam todos os subcomandos).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "ajuda", "usuario", "avatar", "servidor"],
    "Moderacao": ["mod", "logs", "automod", "verificacao"],
    "Comunidade": ["enquete", "sugerir", "sorteio", "anuncio", "cargos", "tickets", "candidaturas", "evento", "lembrete", "aniversario"],
    "Voz e musica": ["musica", "som", "sala", "palco"],
    "Niveis e jogos": ["xp", "interagir", "gif"],
    "Servidor": ["configurar", "boasvindas", "emoji", "cargo_visual"],
}
EMOJIS_CATEGORIAS = {
    "Geral": "📌",
    "Moderacao": "🛡️",
    "Comunidade": "💬",
    "Voz e musica": "🎵",
    "Niveis e jogos": "🎮",
    "Servidor": "⚙️",
    "Clique direito": "🖱️",
}


def _linhas_comando(comando: app_commands.Command | app_commands.Group) -> list[str]:
    if isinstance(comando, app_commands.Group):
        return [f"`/{sub.qualified_name}` - {sub.description}" for sub in comando.commands]
    return [f"`/{comando.name}` - {comando.description}"]


def _texto_categoria(bot: commands.Bot, categoria: str) -> str:
    if categoria == "Clique direito":
        linhas = []
        for tipo, onde in ((discord.AppCommandType.user, "Em uma pessoa"), (discord.AppCommandType.message, "Em uma mensagem")):
            nomes = [menu.name for menu in bot.tree.get_commands(type=tipo)]
            if nomes:
                linhas.append(f"**{onde}:** " + ", ".join(nomes))
        return "Clique com o botao direito > **Apps**:\n" + "\n".join(linhas)

    comandos_por_nome = {comando.name: comando for comando in bot.tree.get_commands()}
    linhas = []
    for nome in COMANDOS_CATEGORIAS[categoria]:
        comando = comandos_por_nome.get(nome)
        if comando:
            linhas += _linhas_comando(comando)
    return "\n".join(linhas)


class PainelAjuda(LayoutViewComRetry):
    def __init__(self, bot: commands.Bot, categoria: str = "Geral"):
        super().__init__(timeout=600)
        self.bot = bot
        seletor = discord.ui.Select(
            placeholder="Escolha uma categoria",
            options=[
                discord.SelectOption(label=nome, emoji=EMOJIS_CATEGORIAS[nome], default=nome == categoria)
                for nome in [*COMANDOS_CATEGORIAS, "Clique direito"]
            ],
        )
        seletor.callback = self._trocar
        self.seletor = seletor
        self.add_item(
            discord.ui.Container(
                layout.texto("## Comandos do bot\n-# Digite `/` no chat para ver os parametros de cada um."),
                layout.linha(seletor),
                layout.separador(),
                layout.texto(f"### {EMOJIS_CATEGORIAS[categoria]} {categoria}\n{_texto_categoria(bot, categoria)}"),
                accent_color=discord.Color.blue(),
            )
        )

    async def _trocar(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=PainelAjuda(self.bot, self.seletor.values[0]))


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
        await interaction.response.send_message(view=PainelAjuda(self.bot), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Geral(bot))
