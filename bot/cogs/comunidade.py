import asyncio
import datetime
import io
import logging
import os
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import banco, layout, sqlitestore
from bot.cogs.moderacao import enviar_log
from bot.config import DATA_DIR, FUSO
from bot.permissions import cargo_perigoso, motivo_cargo_invalido, requer_permissao
from bot.retry_ui import ViewComRetry

logger = logging.getLogger(__name__)

COMUNIDADE_DB_PATH = os.path.join(DATA_DIR, "comunidade.db")
LEMBRETES_MAX_POR_USUARIO = 25
LEMBRETE_MAX_SEGUNDOS = 365 * 24 * 60 * 60
CARGOS_MAX_POR_PAINEL = 20

DURACOES_ENQUETE = [
    app_commands.Choice(name="1 hora", value=1),
    app_commands.Choice(name="4 horas", value=4),
    app_commands.Choice(name="8 horas", value=8),
    app_commands.Choice(name="1 dia", value=24),
    app_commands.Choice(name="3 dias", value=72),
    app_commands.Choice(name="1 semana", value=168),
    app_commands.Choice(name="2 semanas", value=336),
]
DURACOES_EVENTO = [
    app_commands.Choice(name="30 minutos", value=30),
    app_commands.Choice(name="1 hora", value=60),
    app_commands.Choice(name="2 horas", value=120),
    app_commands.Choice(name="3 horas", value=180),
    app_commands.Choice(name="6 horas", value=360),
    app_commands.Choice(name="1 dia", value=1440),
]


# ---------------------------------------------------------------- parsing


_UNIDADES = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def interpretar_duracao(texto: str) -> int | None:
    """'1h30m' / '10 min' / '2d' -> segundos. None se nao for uma duracao."""
    texto = texto.lower().replace(" ", "").replace("min", "m")
    partes = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", texto)
    if not texto or partes is None:
        return None
    total = sum(int(valor) * segundos for valor, segundos in zip(partes.groups(), _UNIDADES.values()) if valor)
    return total or None


def interpretar_data(texto: str, agora: datetime.datetime | None = None) -> datetime.datetime | None:
    """Data/hora no fuso local: '25/12 20:00', '25/12/2026 20:00',
    'hoje 20:00', 'amanha 9h', ou so '20:00' (proxima vez que der 20:00)."""
    agora = agora or datetime.datetime.now(FUSO)
    texto = texto.lower().strip().replace("amanhã", "amanha")
    casamento = re.fullmatch(
        r"(?:(hoje|amanha)|(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?)?\s*(?:as\s*)?(\d{1,2})(?:[:h](\d{2})?)?h?",
        texto,
    )
    if casamento is None:
        return None
    relativo, dia, mes, ano, hora, minuto = casamento.groups()
    hora, minuto = int(hora), int(minuto or 0)
    if hora > 23 or minuto > 59:
        return None

    try:
        if dia:
            ano_int = int(ano) if ano else agora.year
            if ano_int < 100:
                ano_int += 2000
            alvo = datetime.datetime(ano_int, int(mes), int(dia), hora, minuto, tzinfo=FUSO)
            if not ano and alvo < agora:
                alvo = alvo.replace(year=alvo.year + 1)
        else:
            alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if relativo == "amanha":
                alvo += datetime.timedelta(days=1)
            elif relativo is None and alvo <= agora:
                alvo += datetime.timedelta(days=1)
    except ValueError:  # 31/02 e afins
        return None
    return alvo


def interpretar_quando(texto: str, agora: datetime.datetime | None = None) -> datetime.datetime | None:
    """Aceita tanto duracao ('10m') quanto data ('25/12 20:00')."""
    agora = agora or datetime.datetime.now(FUSO)
    segundos = interpretar_duracao(texto)
    if segundos is not None:
        return agora + datetime.timedelta(seconds=segundos)
    return interpretar_data(texto, agora)


def opcoes_enquete(texto: str) -> list[str]:
    return [opcao.strip()[:55] for opcao in texto.split(";") if opcao.strip()]


# ---------------------------------------------------------------- resultado de enquete

banco.registrar_schema(
    "CREATE TABLE IF NOT EXISTS enquetes ("
    "mensagem_id INTEGER PRIMARY KEY, canal_id INTEGER NOT NULL, termina_em REAL NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0)"
)


def enquete_registrar(mensagem_id: int, canal_id: int, termina_em: float) -> None:
    banco.executar(
        "INSERT OR REPLACE INTO enquetes (mensagem_id, canal_id, termina_em) VALUES (?, ?, ?)",
        (mensagem_id, canal_id, termina_em),
    )


def enquetes_vencidas(agora: float) -> list[tuple[int, int, int]]:
    return banco.todos("SELECT mensagem_id, canal_id, tentativas FROM enquetes WHERE termina_em <= ?", (agora,))


def enquete_tentou_de_novo(mensagem_id: int) -> None:
    # O Discord leva alguns segundos para fechar a contagem - tenta de novo
    # no proximo minuto.
    banco.executar("UPDATE enquetes SET tentativas = tentativas + 1, termina_em = termina_em + 60 WHERE mensagem_id = ?", (mensagem_id,))


def enquete_esquecer(mensagem_id: int) -> None:
    banco.executar("DELETE FROM enquetes WHERE mensagem_id = ?", (mensagem_id,))


def cartao_resultado(enquete: discord.Poll) -> discord.ui.LayoutView:
    total = enquete.total_votes
    respostas = sorted(enquete.answers, key=lambda r: r.vote_count, reverse=True)
    maximo = respostas[0].vote_count if respostas else 0
    linhas = []
    for resposta in respostas:
        porcentagem = round(100 * resposta.vote_count / total) if total else 0
        coroa = " 🏆" if total and resposta.vote_count == maximo else ""
        linhas.append(
            f"**{resposta.text}**{coroa}\n`{layout.barra_progresso(resposta.vote_count, total or 1, 16)}` "
            f"{porcentagem}% ({resposta.vote_count})"
        )
    return layout.view_estatica(
        layout.texto(f"## 📊 Resultado: {enquete.question}"),
        layout.separador(),
        layout.texto("\n".join(linhas)),
        layout.texto(f"-# {total} voto(s)"),
        cor=discord.Color.blurple(),
    )


# ---------------------------------------------------------------- lembretes (sqlite)

_comunidade_schemas_prontos: set[str] = set()


def _conectar_bd() -> sqlite3.Connection:
    conexao = sqlite3.connect(COMUNIDADE_DB_PATH)
    if COMUNIDADE_DB_PATH not in _comunidade_schemas_prontos:
        conexao.execute(
            "CREATE TABLE IF NOT EXISTS lembretes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, canal_id INTEGER, "
            "texto TEXT NOT NULL, quando REAL NOT NULL)"
        )
        conexao.execute("CREATE INDEX IF NOT EXISTS lembretes_quando ON lembretes (quando)")
        conexao.commit()
        _comunidade_schemas_prontos.add(COMUNIDADE_DB_PATH)
    return conexao


def lembrete_criar(user_id: int, canal_id: int | None, texto: str, quando: float) -> int | None:
    """Devolve o id do lembrete, ou None se a pessoa ja estiver no limite."""
    with _conectar_bd() as conexao:
        pendentes = conexao.execute("SELECT COUNT(*) FROM lembretes WHERE user_id = ?", (user_id,)).fetchone()[0]
        if pendentes >= LEMBRETES_MAX_POR_USUARIO:
            return None
        cursor = conexao.execute(
            "INSERT INTO lembretes (user_id, canal_id, texto, quando) VALUES (?, ?, ?, ?)",
            (user_id, canal_id, texto, quando),
        )
        conexao.commit()
        return cursor.lastrowid


def lembrete_listar(user_id: int) -> list[tuple[int, str, float]]:
    return sqlitestore.consultar_todos(
        _conectar_bd, "SELECT id, texto, quando FROM lembretes WHERE user_id = ? ORDER BY quando", (user_id,)
    )


def lembrete_cancelar(user_id: int, lembrete_id: int) -> bool:
    return (
        sqlitestore.executar_retornando_rowcount(
            _conectar_bd, "DELETE FROM lembretes WHERE id = ? AND user_id = ?", (lembrete_id, user_id)
        )
        > 0
    )


def lembrete_vencidos(agora: float) -> list[tuple[int, int, int | None, str, float]]:
    return sqlitestore.consultar_todos(
        _conectar_bd, "SELECT id, user_id, canal_id, texto, quando FROM lembretes WHERE quando <= ?", (agora,)
    )


def lembrete_apagar(lembrete_id: int) -> None:
    sqlitestore.executar(_conectar_bd, "DELETE FROM lembretes WHERE id = ?", (lembrete_id,))


# ---------------------------------------------------------------- cargos por botao


class BotaoCargo(discord.ui.DynamicItem[discord.ui.Button], template=r"cargo:(?P<id>\d+)"):
    """Botao de um painel de /cargos: clicou, ganha o cargo; clicou de novo,
    perde. O id do cargo mora no custom_id, entao o painel continua
    funcionando depois que o bot reinicia, sem banco de dados."""

    def __init__(self, cargo_id: int, rotulo: str = "Cargo"):
        super().__init__(
            discord.ui.Button(label=rotulo[:80], style=discord.ButtonStyle.secondary, custom_id=f"cargo:{cargo_id}")
        )
        self.cargo_id = cargo_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["id"]), item.label or "Cargo")

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        cargo = guild.get_role(self.cargo_id) if guild else None
        membro = interaction.user
        if cargo is None or not isinstance(membro, discord.Member):
            await interaction.response.send_message("Esse cargo nao existe mais.", ephemeral=True)
            return
        # Confere de novo na hora do clique: as permissoes do cargo podem ter
        # mudado depois que o painel foi publicado.
        if cargo.managed or cargo >= guild.me.top_role or cargo_perigoso(cargo):
            await interaction.response.send_message(
                "Esse cargo nao pode mais ser pego por aqui - avise a moderacao.", ephemeral=True
            )
            return

        if cargo in membro.roles:
            await membro.remove_roles(cargo, reason="Painel de cargos")
            await interaction.response.send_message(f"Cargo {cargo.mention} removido.", ephemeral=True)
        else:
            await membro.add_roles(cargo, reason="Painel de cargos")
            await interaction.response.send_message(f"Voce agora tem o cargo {cargo.mention}.", ephemeral=True)


def painel_cargos(titulo: str, descricao: str | None, cargos: list[discord.Role]) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(
        layout.texto(f"## {titulo}\n{descricao or 'Clique para pegar ou largar um cargo.'}"),
        accent_color=cargos[0].color if cargos[0].color.value else discord.Color.blurple(),
    )
    for inicio in range(0, len(cargos), 5):
        container.add_item(layout.linha(*(BotaoCargo(cargo.id, cargo.name) for cargo in cargos[inicio : inicio + 5])))
    view.add_item(container)
    return view


class EscolherCargosPainel(ViewComRetry):
    def __init__(self, titulo: str, descricao: str | None):
        super().__init__(timeout=600)
        self.titulo = titulo
        self.descricao = descricao
        self.select = discord.ui.RoleSelect(
            placeholder="Escolha os cargos do painel", min_values=1, max_values=CARGOS_MAX_POR_PAINEL
        )
        self.select.callback = self._escolhidos
        self.add_item(self.select)

    async def _escolhidos(self, interaction: discord.Interaction):
        cargos = sorted(self.select.values, key=lambda cargo: cargo.position, reverse=True)
        invalidos = [(cargo, motivo_cargo_invalido(cargo, interaction.user)) for cargo in cargos]
        invalidos = [(cargo, motivo) for cargo, motivo in invalidos if motivo]
        if invalidos:
            linhas = "\n".join(f"- {cargo.mention}: {motivo}" for cargo, motivo in invalidos)
            await interaction.response.send_message(
                f"Esses cargos nao podem ir para o painel:\n{linhas}", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(view=painel_cargos(self.titulo, self.descricao, cargos))
        self.stop()
        await interaction.followup.send("Painel publicado neste canal.", ephemeral=True)


# ---------------------------------------------------------------- tickets


def _nome_ticket(usuario: discord.abc.User) -> str:
    return f"ticket-{usuario.name}"[:100]


class BotaoAbrirTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:abrir:(?P<cargo>\d+)"):
    def __init__(self, cargo_id: int):
        super().__init__(
            discord.ui.Button(
                label="Abrir ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id=f"ticket:abrir:{cargo_id}"
            )
        )
        self.cargo_id = cargo_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["cargo"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        canal = interaction.channel
        if not isinstance(canal, discord.TextChannel):
            await interaction.response.send_message("Tickets so funcionam em canais de texto.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        nome = _nome_ticket(interaction.user)
        existente = discord.utils.find(lambda t: t.name == nome and not t.archived, canal.threads)
        if existente is not None:
            await interaction.followup.send(f"Voce ja tem um ticket aberto: {existente.mention}", ephemeral=True)
            return

        thread = await canal.create_thread(
            name=nome,
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=10080,
            reason=f"Ticket aberto por {interaction.user}",
        )
        await thread.add_user(interaction.user)

        equipe = f"<@&{self.cargo_id}>" if self.cargo_id else "A moderacao"
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                layout.texto(
                    f"## Ticket de {interaction.user.display_name}\n"
                    f"{interaction.user.mention}, descreva aqui o que voce precisa. "
                    f"{equipe} vai responder assim que puder.\n"
                    "-# So voce e a equipe veem esta conversa."
                ),
                layout.linha(BotaoFecharTicket(interaction.user.id), BotaoExportarTicket()),
                accent_color=discord.Color.blurple(),
            )
        )
        # Mencionar o usuario e o cargo de suporte e o que os coloca dentro
        # da thread privada.
        await thread.send(view=view, allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.followup.send(f"Ticket criado: {thread.mention}", ephemeral=True)


class BotaoFecharTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:fechar:(?P<dono>\d+)"):
    def __init__(self, dono_id: int):
        super().__init__(
            discord.ui.Button(label="Fechar ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id=f"ticket:fechar:{dono_id}")
        )
        self.dono_id = dono_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["dono"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Isso so funciona dentro de um ticket.", ephemeral=True)
            return
        pode = interaction.user.id == self.dono_id or interaction.permissions.manage_threads
        if not pode:
            await interaction.response.send_message("So quem abriu o ticket ou a moderacao pode fechar.", ephemeral=True)
            return
        await interaction.response.send_message(f"Ticket fechado por {interaction.user.mention}.")

        # Guarda a conversa no canal de logs e manda uma copia para quem abriu.
        conteudo = await transcrever_ticket(thread)
        await enviar_log(
            interaction.guild,
            "Ticket fechado",
            f"**Ticket:** {thread.mention} ({thread.name})\n**Aberto por:** <@{self.dono_id}>\n"
            f"**Fechado por:** {interaction.user.mention}",
            discord.Color.dark_grey(),
            arquivo=discord.File(io.BytesIO(conteudo), filename=f"{thread.name}.txt"),
        )
        dono = interaction.guild.get_member(self.dono_id)
        if dono is not None:
            try:
                await dono.send(
                    f"Seu ticket em **{interaction.guild.name}** foi fechado. Segue a conversa:",
                    file=discord.File(io.BytesIO(conteudo), filename=f"{thread.name}.txt"),
                )
            except discord.HTTPException:
                pass
        await thread.edit(archived=True, locked=True, reason=f"Ticket fechado por {interaction.user}")


class BotaoExportarTicket(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:exportar"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Baixar conversa", emoji="📄", style=discord.ButtonStyle.secondary, custom_id="ticket:exportar")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("Isso so funciona dentro de um ticket.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        conteudo = await transcrever_ticket(interaction.channel)
        await interaction.followup.send(
            file=discord.File(io.BytesIO(conteudo), filename=f"{interaction.channel.name}.txt"), ephemeral=True
        )


async def transcrever_ticket(thread: discord.Thread, limite: int = 2000) -> bytes:
    """Conversa do ticket em texto puro (mais antigas primeiro)."""
    linhas = [f"Ticket: {thread.name}", f"Servidor: {thread.guild.name}", ""]
    async for mensagem in thread.history(limit=limite, oldest_first=True):
        quando = mensagem.created_at.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")
        texto = mensagem.content
        if not texto and mensagem.components:
            texto = "(painel do bot)"
        anexos = " ".join(anexo.url for anexo in mensagem.attachments)
        linhas.append(f"[{quando}] {mensagem.author}: {texto} {anexos}".rstrip())
    return "\n".join(linhas).encode("utf-8")


def painel_tickets(mensagem: str | None, cargo_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.Section(
                "## Suporte\n" + (mensagem or "Precisa falar com a moderacao? Abra um ticket privado."),
                accessory=BotaoAbrirTicket(cargo_id),
            ),
            accent_color=discord.Color.blurple(),
        )
    )
    return view


# ---------------------------------------------------------------- cog


class Comunidade(commands.Cog):
    lembrete = app_commands.Group(
        name="lembrete",
        description="Lembretes que o bot te manda na hora marcada",
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.entregar_lembretes.start()
        self.resultados_enquetes.start()

    async def cog_unload(self) -> None:
        self.entregar_lembretes.cancel()
        self.resultados_enquetes.cancel()

    @tasks.loop(minutes=1)
    async def resultados_enquetes(self):
        for mensagem_id, canal_id, tentativas in await asyncio.to_thread(enquetes_vencidas, time.time()):
            canal = self.bot.get_channel(canal_id)
            if canal is None or tentativas >= 5:
                await asyncio.to_thread(enquete_esquecer, mensagem_id)
                continue
            try:
                mensagem = await canal.fetch_message(mensagem_id)
            except discord.HTTPException:
                await asyncio.to_thread(enquete_esquecer, mensagem_id)
                continue
            if mensagem.poll is None or not mensagem.poll.is_finalised():
                await asyncio.to_thread(enquete_tentou_de_novo, mensagem_id)
                continue
            await asyncio.to_thread(enquete_esquecer, mensagem_id)
            try:
                await mensagem.reply(view=cartao_resultado(mensagem.poll), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as erro:
                logger.warning(f"Nao consegui publicar o resultado da enquete {mensagem_id}: {erro}")

    @resultados_enquetes.before_loop
    async def _antes_enquetes(self):
        await self.bot.wait_until_ready()

    # ---- /enquete

    @app_commands.command(name="enquete", description="Cria uma enquete nativa do Discord")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(
        pergunta="A pergunta",
        opcoes="De 2 a 10 opcoes separadas por ponto e virgula (ex: Pizza; Hamburguer; Sushi)",
        duracao="Quanto tempo a enquete fica aberta",
        multipla="Pode votar em mais de uma opcao?",
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.choices(duracao=DURACOES_ENQUETE)
    async def enquete(
        self,
        interaction: discord.Interaction,
        pergunta: app_commands.Range[str, 1, 300],
        opcoes: str,
        duracao: app_commands.Choice[int] | None = None,
        multipla: bool = False,
    ):
        lista = opcoes_enquete(opcoes)
        if not 2 <= len(lista) <= 10:
            await interaction.response.send_message(
                "Coloque de 2 a 10 opcoes separadas por ponto e virgula (;).", ephemeral=True
            )
            return
        enquete = discord.Poll(
            question=pergunta, duration=datetime.timedelta(hours=duracao.value if duracao else 24), multiple=multipla
        )
        for opcao in lista:
            enquete.add_answer(text=opcao)
        await interaction.response.send_message(poll=enquete)
        # Guarda para publicar o resultado completo quando acabar (so da se
        # o bot estiver no servidor/canal - nao em instalacao de usuario).
        if interaction.guild is not None and interaction.is_guild_integration():
            mensagem = await interaction.original_response()
            termina = time.time() + enquete.duration.total_seconds()
            await asyncio.to_thread(enquete_registrar, mensagem.id, interaction.channel_id, termina)

    # ---- /cargos e /tickets

    @app_commands.command(name="cargos", description="Publica um painel onde os membros pegam cargos com um clique")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(titulo="Titulo do painel", descricao="Texto explicando os cargos (opcional)")
    async def cargos(
        self,
        interaction: discord.Interaction,
        titulo: app_commands.Range[str, 1, 100],
        descricao: app_commands.Range[str, 1, 1000] | None = None,
    ):
        if not await requer_permissao(interaction, "manage_roles", "esse comando"):
            return
        await interaction.response.send_message(
            f"Escolha ate {CARGOS_MAX_POR_PAINEL} cargos. O painel sera publicado neste canal.",
            view=EscolherCargosPainel(titulo, descricao),
            ephemeral=True,
        )

    @app_commands.command(name="tickets", description="Publica um painel para membros abrirem tickets privados com a equipe")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        cargo_suporte="Cargo da equipe que entra automaticamente nos tickets",
        mensagem="Texto do painel (opcional)",
    )
    async def tickets(
        self,
        interaction: discord.Interaction,
        cargo_suporte: discord.Role | None = None,
        mensagem: app_commands.Range[str, 1, 1000] | None = None,
    ):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use esse comando num canal de texto.", ephemeral=True)
            return
        if not interaction.app_permissions.create_private_threads:
            await interaction.response.send_message(
                "Preciso da permissao Criar Threads Privadas neste canal.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(view=painel_tickets(mensagem, cargo_suporte.id if cargo_suporte else 0))
        await interaction.followup.send("Painel de tickets publicado neste canal.", ephemeral=True)

    # ---- /evento

    @app_commands.command(name="evento", description="Cria um evento agendado no servidor")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_events=True)
    @app_commands.describe(
        nome="Nome do evento",
        quando="Data e hora (ex: 25/12 20:00, amanha 21h, hoje 19:30)",
        duracao="Quanto tempo dura",
        canal="Canal de voz/palco do evento (vazio = evento fora do Discord)",
        local="Onde vai ser, se nao for num canal de voz",
        descricao="Detalhes do evento",
        imagem="Imagem de capa",
    )
    @app_commands.choices(duracao=DURACOES_EVENTO)
    async def evento(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 1, 100],
        quando: str,
        duracao: app_commands.Choice[int] | None = None,
        canal: discord.VoiceChannel | discord.StageChannel | None = None,
        local: app_commands.Range[str, 1, 100] | None = None,
        descricao: app_commands.Range[str, 1, 1000] | None = None,
        imagem: discord.Attachment | None = None,
    ):
        if not await requer_permissao(interaction, "manage_events", "esse comando"):
            return
        inicio = interpretar_data(quando)
        if inicio is None or inicio <= datetime.datetime.now(FUSO):
            await interaction.response.send_message(
                "Nao entendi a data ou ela ja passou. Exemplos: `25/12 20:00`, `amanha 21h`, `hoje 19:30`.",
                ephemeral=True,
            )
            return
        if imagem is not None and not (imagem.content_type or "").startswith("image/"):
            await interaction.response.send_message("A capa precisa ser uma imagem.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        fim = inicio + datetime.timedelta(minutes=duracao.value if duracao else 60)
        extras = {}
        if descricao:
            extras["description"] = descricao
        if imagem is not None:
            extras["image"] = await imagem.read()

        if canal is not None:
            tipo = discord.EntityType.stage_instance if isinstance(canal, discord.StageChannel) else discord.EntityType.voice
            evento = await interaction.guild.create_scheduled_event(
                name=nome, start_time=inicio, end_time=fim, entity_type=tipo, channel=canal,
                privacy_level=discord.PrivacyLevel.guild_only, **extras,
            )
        else:
            evento = await interaction.guild.create_scheduled_event(
                name=nome, start_time=inicio, end_time=fim, entity_type=discord.EntityType.external,
                location=local or "A definir", privacy_level=discord.PrivacyLevel.guild_only, **extras,
            )
        await interaction.followup.send(f"Evento criado: {evento.url}", ephemeral=True)

    # ---- /lembrete

    @lembrete.command(name="criar", description="Cria um lembrete")
    @app_commands.checks.cooldown(5, 60, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(
        quando="Daqui quanto tempo (10m, 2h, 1d) ou quando (25/12 20:00, amanha 9h)",
        texto="Do que lembrar",
    )
    async def lembrete_criar_cmd(
        self, interaction: discord.Interaction, quando: str, texto: app_commands.Range[str, 1, 1000]
    ):
        alvo = interpretar_quando(quando)
        agora = datetime.datetime.now(FUSO)
        if alvo is None or alvo <= agora:
            await interaction.response.send_message(
                "Nao entendi quando. Exemplos: `10m`, `1h30m`, `2d`, `25/12 20:00`, `amanha 9h`.", ephemeral=True
            )
            return
        if (alvo - agora).total_seconds() > LEMBRETE_MAX_SEGUNDOS:
            await interaction.response.send_message("O limite e de 1 ano.", ephemeral=True)
            return

        # So da pra avisar no canal se o bot estiver nele (instalado no
        # servidor ou na DM com o bot); senao o aviso vai por DM.
        no_canal = interaction.guild is not None and interaction.is_guild_integration()
        canal_id = interaction.channel_id if no_canal else None
        lembrete_id = await asyncio.to_thread(lembrete_criar, interaction.user.id, canal_id, texto, alvo.timestamp())
        if lembrete_id is None:
            await interaction.response.send_message(
                f"Voce ja tem {LEMBRETES_MAX_POR_USUARIO} lembretes pendentes. Cancele algum antes.", ephemeral=True
            )
            return
        onde = "aqui" if canal_id else "por DM"
        await interaction.response.send_message(
            f"Combinado! Te lembro {discord.utils.format_dt(alvo, 'R')} ({discord.utils.format_dt(alvo, 'f')}) {onde}.",
            ephemeral=True,
        )

    @lembrete.command(name="listar", description="Mostra seus lembretes pendentes")
    async def lembrete_listar_cmd(self, interaction: discord.Interaction):
        lembretes = await asyncio.to_thread(lembrete_listar, interaction.user.id)
        if not lembretes:
            await interaction.response.send_message("Voce nao tem lembretes pendentes.", ephemeral=True)
            return
        linhas = [
            f"`#{lembrete_id}` {discord.utils.format_dt(datetime.datetime.fromtimestamp(quando, FUSO), 'R')} - {texto[:100]}"
            for lembrete_id, texto, quando in lembretes
        ]
        await interaction.response.send_message(
            view=layout.view_estatica(layout.texto("## Seus lembretes\n" + "\n".join(linhas))), ephemeral=True
        )

    @lembrete.command(name="cancelar", description="Cancela um lembrete pendente")
    @app_commands.describe(lembrete="Qual lembrete cancelar")
    async def lembrete_cancelar_cmd(self, interaction: discord.Interaction, lembrete: int):
        removido = await asyncio.to_thread(lembrete_cancelar, interaction.user.id, lembrete)
        texto = "Lembrete cancelado." if removido else "Nao achei esse lembrete entre os seus."
        await interaction.response.send_message(texto, ephemeral=True)

    @lembrete_cancelar_cmd.autocomplete("lembrete")
    async def _lembrete_autocomplete(self, interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[int]]:
        lembretes = await asyncio.to_thread(lembrete_listar, interaction.user.id)
        return [
            app_commands.Choice(name=f"#{lembrete_id} - {texto}"[:100], value=lembrete_id)
            for lembrete_id, texto, _ in lembretes
            if atual.lower() in texto.lower() or atual == str(lembrete_id)
        ][:25]

    @tasks.loop(seconds=20)
    async def entregar_lembretes(self):
        vencidos = await asyncio.to_thread(lembrete_vencidos, time.time())
        for lembrete_id, user_id, canal_id, texto, quando in vencidos:
            # Apaga antes de enviar: se o envio falhar de vez, melhor perder
            # um lembrete do que mandar o mesmo a cada 20s para sempre.
            await asyncio.to_thread(lembrete_apagar, lembrete_id)
            aviso = f"⏰ <@{user_id}>, lembrete: {texto}"
            mencoes = discord.AllowedMentions(users=[discord.Object(user_id)])
            canal = self.bot.get_channel(canal_id) if canal_id else None
            try:
                if canal is not None:
                    await canal.send(aviso, allowed_mentions=mencoes)
                    continue
            except discord.HTTPException:
                pass  # canal sem permissao/apagado - cai para a DM
            try:
                usuario = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await usuario.send(aviso)
            except discord.HTTPException as erro:
                logger.warning(f"Nao consegui entregar o lembrete {lembrete_id} para {user_id}: {erro}")

    @entregar_lembretes.before_loop
    async def _antes_lembretes(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoCargo, BotaoAbrirTicket, BotaoFecharTicket, BotaoExportarTicket)
    await bot.add_cog(Comunidade(bot))
