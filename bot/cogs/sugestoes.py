import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import banco, configstore, layout

logger = logging.getLogger(__name__)

CHAVE_CANAL = "sugestoes_canal"

STATUS = {
    "analise": ("Em analise", "🕓", discord.Color.blurple()),
    "aprovada": ("Aprovada", "✅", discord.Color.green()),
    "recusada": ("Recusada", "❌", discord.Color.red()),
}

banco.registrar_schema(
    """
    CREATE TABLE IF NOT EXISTS sugestoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        autor_id INTEGER NOT NULL,
        texto TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'analise',
        resposta TEXT
    );
    CREATE TABLE IF NOT EXISTS sugestao_votos (
        sugestao_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        voto INTEGER NOT NULL,
        PRIMARY KEY (sugestao_id, user_id)
    );
    """
)


# ---------------------------------------------------------------- banco


def sugestao_criar(guild_id: int, autor_id: int, texto: str) -> int:
    with banco.conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO sugestoes (guild_id, autor_id, texto) VALUES (?, ?, ?)", (guild_id, autor_id, texto)
        )
        conexao.commit()
        return cursor.lastrowid


def sugestao_obter(sugestao_id: int) -> dict | None:
    linha = banco.um(
        "SELECT id, guild_id, autor_id, texto, status, resposta FROM sugestoes WHERE id = ?", (sugestao_id,)
    )
    if linha is None:
        return None
    sugestao = dict(zip(("id", "guild_id", "autor_id", "texto", "status", "resposta"), linha))
    votos = dict(
        banco.todos("SELECT voto, COUNT(*) FROM sugestao_votos WHERE sugestao_id = ? GROUP BY voto", (sugestao_id,))
    )
    sugestao["sim"] = votos.get(1, 0)
    sugestao["nao"] = votos.get(-1, 0)
    return sugestao


def sugestao_votar(sugestao_id: int, user_id: int, voto: int) -> int:
    """Registra o voto (+1/-1). Votar de novo no mesmo tira o voto; votar no
    outro troca. Devolve o voto que ficou (0 = nenhum)."""
    atual = banco.um(
        "SELECT voto FROM sugestao_votos WHERE sugestao_id = ? AND user_id = ?", (sugestao_id, user_id)
    )
    if atual and atual[0] == voto:
        banco.executar("DELETE FROM sugestao_votos WHERE sugestao_id = ? AND user_id = ?", (sugestao_id, user_id))
        return 0
    banco.executar(
        "INSERT INTO sugestao_votos (sugestao_id, user_id, voto) VALUES (?, ?, ?) "
        "ON CONFLICT (sugestao_id, user_id) DO UPDATE SET voto = excluded.voto",
        (sugestao_id, user_id, voto),
    )
    return voto


def sugestao_definir_status(sugestao_id: int, status: str, resposta: str | None) -> None:
    banco.executar("UPDATE sugestoes SET status = ?, resposta = ? WHERE id = ?", (status, resposta, sugestao_id))


# ---------------------------------------------------------------- ui


class BotaoSugestao(
    discord.ui.DynamicItem[discord.ui.Button], template=r"sugestao:(?P<id>\d+):(?P<acao>sim|nao|aprovar|recusar)"
):
    ESTILOS = {
        "sim": ("👍", discord.ButtonStyle.success),
        "nao": ("👎", discord.ButtonStyle.danger),
        "aprovar": ("Aprovar", discord.ButtonStyle.secondary),
        "recusar": ("Recusar", discord.ButtonStyle.secondary),
    }

    def __init__(self, sugestao_id: int, acao: str, contagem: int | None = None, desativado: bool = False):
        rotulo, estilo = self.ESTILOS[acao]
        if contagem is not None:
            rotulo = f"{rotulo} {contagem}"
        super().__init__(
            discord.ui.Button(label=rotulo, style=estilo, custom_id=f"sugestao:{sugestao_id}:{acao}", disabled=desativado)
        )
        self.sugestao_id = sugestao_id
        self.acao = acao

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["id"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.acao in ("aprovar", "recusar"):
            if not interaction.permissions.manage_guild:
                await interaction.response.send_message(
                    "So quem tem Gerenciar Servidor pode aprovar ou recusar.", ephemeral=True
                )
                return
            await interaction.response.send_modal(ModalRespostaSugestao(self.sugestao_id, self.acao))
            return

        voto = 1 if self.acao == "sim" else -1
        resultado = await asyncio.to_thread(sugestao_votar, self.sugestao_id, interaction.user.id, voto)
        sugestao = await asyncio.to_thread(sugestao_obter, self.sugestao_id)
        if sugestao is None:
            await interaction.response.send_message("Essa sugestao nao existe mais.", ephemeral=True)
            return
        await interaction.response.edit_message(view=painel_sugestao(sugestao), allowed_mentions=discord.AllowedMentions.none())
        texto = {1: "Voto a favor registrado.", -1: "Voto contra registrado.", 0: "Voto retirado."}[resultado]
        await interaction.followup.send(texto, ephemeral=True)


class ModalRespostaSugestao(discord.ui.Modal):
    resposta = discord.ui.TextInput(
        label="Resposta para quem sugeriu (opcional)", style=discord.TextStyle.paragraph, required=False, max_length=500
    )

    def __init__(self, sugestao_id: int, acao: str):
        super().__init__(title="Aprovar sugestao" if acao == "aprovar" else "Recusar sugestao")
        self.sugestao_id = sugestao_id
        self.status = "aprovada" if acao == "aprovar" else "recusada"

    async def on_submit(self, interaction: discord.Interaction):
        resposta = str(self.resposta).strip() or None
        await asyncio.to_thread(sugestao_definir_status, self.sugestao_id, self.status, resposta)
        sugestao = await asyncio.to_thread(sugestao_obter, self.sugestao_id)
        await interaction.response.edit_message(view=painel_sugestao(sugestao), allowed_mentions=discord.AllowedMentions.none())

        if isinstance(interaction.channel, discord.Thread) and isinstance(interaction.channel.parent, discord.ForumChannel):
            await _aplicar_tag(interaction.channel, self.status)

        nome, emoji, _ = STATUS[self.status]
        autor = interaction.guild.get_member(sugestao["autor_id"])
        if autor is not None:
            try:
                await autor.send(
                    f"{emoji} Sua sugestao #{sugestao['id']} em **{interaction.guild.name}** foi {nome.lower()}."
                    + (f"\nResposta: {resposta}" if resposta else "")
                )
            except discord.HTTPException:
                pass


def painel_sugestao(sugestao: dict) -> discord.ui.LayoutView:
    nome, emoji, cor = STATUS[sugestao["status"]]
    aberta = sugestao["status"] == "analise"
    total = sugestao["sim"] + sugestao["nao"]
    aprovacao = f" ({round(100 * sugestao['sim'] / total)}% a favor)" if total else ""

    container = discord.ui.Container(
        layout.texto(f"## Sugestao #{sugestao['id']}\n{sugestao['texto']}"),
        layout.texto(f"-# Por <@{sugestao['autor_id']}> | {emoji} {nome}{aprovacao}"),
        accent_color=cor,
    )
    if sugestao["resposta"]:
        container.add_item(layout.separador())
        container.add_item(layout.texto(f"**Resposta da equipe:** {sugestao['resposta']}"))
    container.add_item(
        layout.linha(
            BotaoSugestao(sugestao["id"], "sim", sugestao["sim"], desativado=not aberta),
            BotaoSugestao(sugestao["id"], "nao", sugestao["nao"], desativado=not aberta),
            BotaoSugestao(sugestao["id"], "aprovar", desativado=not aberta),
            BotaoSugestao(sugestao["id"], "recusar", desativado=not aberta),
        )
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


async def _tags_forum(forum: discord.ForumChannel) -> dict[str, discord.ForumTag]:
    """Garante as etiquetas de status no forum (cria as que faltarem, se o
    bot puder) e devolve status -> etiqueta."""
    tags: dict[str, discord.ForumTag] = {}
    for status, (nome, emoji, _) in STATUS.items():
        tag = discord.utils.get(forum.available_tags, name=nome)
        if tag is None and forum.permissions_for(forum.guild.me).manage_channels:
            try:
                tag = await forum.create_tag(name=nome, emoji=discord.PartialEmoji(name=emoji), moderated=True)
            except discord.HTTPException as erro:
                logger.warning(f"Nao consegui criar a etiqueta '{nome}' no forum {forum.id}: {erro}")
        if tag is not None:
            tags[status] = tag
    return tags


async def _aplicar_tag(thread: discord.Thread, status: str) -> None:
    tags = await _tags_forum(thread.parent)
    if status not in tags:
        return
    outras = [tag for tag in thread.applied_tags if tag.id not in {t.id for t in tags.values()}]
    try:
        await thread.edit(applied_tags=(outras + [tags[status]])[:5])
    except discord.HTTPException as erro:
        logger.warning(f"Nao consegui mudar a etiqueta da sugestao {thread.id}: {erro}")


# ---------------------------------------------------------------- cog


class Sugestoes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="sugerir", description="Manda uma sugestao para o servidor votar")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 120, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(texto="Sua sugestao")
    async def sugerir(self, interaction: discord.Interaction, texto: app_commands.Range[str, 10, 1500]):
        canal_id = await asyncio.to_thread(configstore.obter, interaction.guild.id, CHAVE_CANAL)
        canal = interaction.guild.get_channel(canal_id) if canal_id else None
        if canal is None:
            await interaction.response.send_message(
                "Este servidor ainda nao tem canal de sugestoes. Um admin configura em `/configurar sugestoes`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        sugestao_id = await asyncio.to_thread(sugestao_criar, interaction.guild.id, interaction.user.id, texto)
        sugestao = await asyncio.to_thread(sugestao_obter, sugestao_id)
        view = painel_sugestao(sugestao)
        sem_mencoes = discord.AllowedMentions.none()

        if isinstance(canal, discord.ForumChannel):
            tags = await _tags_forum(canal)
            criado = await canal.create_thread(
                name=f"#{sugestao_id} - {texto}"[:100],
                view=view,
                applied_tags=[tags["analise"]] if "analise" in tags else [],
                allowed_mentions=sem_mencoes,
            )
            link = criado.thread.mention
        else:
            mensagem = await canal.send(view=view, allowed_mentions=sem_mencoes)
            # Thread para discutir a sugestao sem poluir o canal.
            try:
                await mensagem.create_thread(name=f"Sugestao #{sugestao_id}", auto_archive_duration=10080)
            except discord.HTTPException:
                pass
            link = mensagem.jump_url
        await interaction.followup.send(f"Sugestao enviada: {link}", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoSugestao)
    await bot.add_cog(Sugestoes(bot))
