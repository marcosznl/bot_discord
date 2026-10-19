import discord
from discord import app_commands
from discord.ext import commands

from bot import layout
from bot.retry_ui import LayoutViewDoBot

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


Comando = app_commands.Command | app_commands.Group | app_commands.ContextMenu


def _raiz(comando: Comando) -> Comando:
    # Permissoes e contextos de um subcomando (/mod banir) vem do grupo.
    return getattr(comando, "root_parent", None) or comando


def comando_restrito(comando: Comando) -> bool:
    """Comando que o Discord ja esconde do menu do / de quem nao tem a
    permissao padrao dele (comandos de moderacao e administracao)."""
    exigida = _raiz(comando).default_permissions
    return exigida is not None and exigida.value != 0


def pode_ver(comando: Comando, permissoes: discord.Permissions, no_servidor: bool) -> bool:
    """Mesma regra do menu do /: fora de um servidor com o bot so aparecem
    os comandos de instalacao de usuario; no servidor, os restritos so
    para quem tem a permissao padrao deles."""
    raiz = _raiz(comando)
    if not no_servidor:
        instalacao = raiz.allowed_installs
        return instalacao is not None and bool(instalacao.user)
    exigida = raiz.default_permissions
    return exigida is None or exigida.is_subset(permissoes)


def _texto_categoria(
    bot: commands.Bot, categoria: str, permissoes: discord.Permissions, no_servidor: bool
) -> tuple[str, bool] | None:
    """Texto da categoria com so o que a pessoa pode usar, e se tudo nela e
    so para a equipe. None se nao sobrar nada para mostrar."""
    if categoria == "Clique direito":
        linhas, restritos, total = [], 0, 0
        for tipo, onde in ((discord.AppCommandType.user, "Em uma pessoa"), (discord.AppCommandType.message, "Em uma mensagem")):
            menus = [m for m in bot.tree.get_commands(type=tipo) if pode_ver(m, permissoes, no_servidor)]
            if menus:
                nomes = [f"🔒 {m.name}" if comando_restrito(m) else m.name for m in menus]
                linhas.append(f"**{onde}:** " + ", ".join(nomes))
                restritos += sum(comando_restrito(m) for m in menus)
                total += len(menus)
        if not linhas:
            return None
        return "Clique com o botao direito > **Apps**:\n" + "\n".join(linhas), restritos == total

    comandos_por_nome = {comando.name: comando for comando in bot.tree.get_commands()}
    itens: list[Comando] = []
    for nome in COMANDOS_CATEGORIAS[categoria]:
        comando = comandos_por_nome.get(nome)
        if comando is None:
            continue
        itens += comando.commands if isinstance(comando, app_commands.Group) else [comando]
    visiveis = [c for c in itens if pode_ver(c, permissoes, no_servidor)]
    if not visiveis:
        return None
    linhas = [
        f"{'🔒 ' if comando_restrito(c) else ''}`/{c.qualified_name}` - {c.description}" for c in visiveis
    ]
    return "\n".join(linhas), all(comando_restrito(c) for c in visiveis)


class PainelAjuda(LayoutViewDoBot):
    def __init__(
        self,
        bot: commands.Bot,
        permissoes: discord.Permissions,
        no_servidor: bool,
        categoria: str | None = None,
    ):
        super().__init__(timeout=600)
        self.bot = bot
        self.permissoes = permissoes
        self.no_servidor = no_servidor

        textos = {
            nome: texto
            for nome in [*COMANDOS_CATEGORIAS, "Clique direito"]
            if (texto := _texto_categoria(bot, nome, permissoes, no_servidor)) is not None
        }
        if categoria not in textos:
            categoria = next(iter(textos))
        corpo, so_equipe = textos[categoria]
        alguma_restrita = any("🔒" in texto for texto, _ in textos.values())

        seletor = discord.ui.Select(
            placeholder="Escolha uma categoria",
            options=[
                discord.SelectOption(
                    label=nome,
                    emoji=EMOJIS_CATEGORIAS[nome],
                    description="So para a equipe" if equipe else None,
                    default=nome == categoria,
                )
                for nome, (_, equipe) in textos.items()
            ],
        )
        seletor.callback = self._trocar
        self.seletor = seletor

        cabecalho = "## Comandos do bot\n-# Digite `/` no chat para ver os parametros de cada um."
        if not no_servidor:
            cabecalho += "\n-# Fora de um servidor com o Arco, so estes comandos funcionam."
        titulo = f"### {EMOJIS_CATEGORIAS[categoria]} {categoria}" + (" · so para a equipe" if so_equipe else "")
        container = discord.ui.Container(
            layout.texto(cabecalho),
            layout.linha(seletor),
            layout.separador(),
            layout.texto(f"{titulo}\n{corpo}"),
            accent_color=discord.Color.blue(),
        )
        if alguma_restrita:
            container.add_item(layout.texto("-# 🔒 So aparece para quem tem a permissao (moderacao/administracao)."))
        self.add_item(container)

    async def _trocar(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=PainelAjuda(self.bot, self.permissoes, self.no_servidor, self.seletor.values[0])
        )


def painel_ajuda_para(bot: commands.Bot, interaction: discord.Interaction) -> PainelAjuda:
    # Com o bot no servidor valem as permissoes da pessoa no canal; sem ele
    # (DM, ou app instalado so na conta), so os comandos de usuario.
    no_servidor = interaction.guild is not None and interaction.is_guild_integration()
    return PainelAjuda(bot, interaction.permissions, no_servidor)


class Geral(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Testa se o bot esta online e mostra a latencia")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Pong! {round(self.bot.latency * 1000)} ms")

    @app_commands.command(name="ajuda", description="Mostra os comandos do bot que voce pode usar, por categoria")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def ajuda(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=painel_ajuda_para(self.bot, interaction), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Geral(bot))
