import asyncio
import datetime
import json
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import banco, configstore, layout
from bot.cogs.comunidade import interpretar_quando
from bot.config import DATA_DIR, FUSO
from bot.permissions import requer_permissao
from bot.retry_ui import ModalDoBot

logger = logging.getLogger(__name__)

PASTA_IMAGENS = os.path.join(DATA_DIR, "anuncios")
TAMANHO_MAX_IMAGEM = 8 * 1024 * 1024
# {"canal": id ou None, "cargo": id ou None, "minutos": int}
CHAVE_EVENTOS = "eventos_avisos"

banco.registrar_schema(
    """
    CREATE TABLE IF NOT EXISTS anuncios_agendados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        canal_id INTEGER NOT NULL,
        autor_id INTEGER NOT NULL,
        dados TEXT NOT NULL,
        quando REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS eventos_avisados (evento_id INTEGER PRIMARY KEY);
    """
)


# ---------------------------------------------------------------- anuncios


def interpretar_botao(texto: str) -> tuple[str, str] | None:
    """'Inscreva-se | https://site' -> ('Inscreva-se', 'https://site')."""
    rotulo, separador, url = texto.partition("|")
    rotulo, url = rotulo.strip()[:80], url.strip()
    if not separador or not rotulo or not url.startswith(("https://", "http://")):
        return None
    return rotulo, url


def montar_anuncio(dados: dict) -> discord.ui.LayoutView:
    itens: list[discord.ui.Item] = [layout.texto(f"## {dados['titulo']}\n{dados['texto']}")]
    if dados.get("imagem"):
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{dados['imagem']}")))
    if dados.get("botao"):
        rotulo, url = dados["botao"]
        itens.append(layout.linha(discord.ui.Button(label=rotulo, url=url)))
    if dados.get("cargo"):
        itens.append(layout.texto(f"-# <@&{dados['cargo']}>"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*itens, accent_color=dados.get("cor") or discord.Color.blurple().value))
    return view


async def enviar_anuncio(canal: discord.abc.Messageable, dados: dict, caminho_imagem: str | None) -> discord.Message:
    arquivos = [discord.File(caminho_imagem, filename=dados["imagem"])] if caminho_imagem else []
    mencoes = discord.AllowedMentions(roles=[discord.Object(dados["cargo"])]) if dados.get("cargo") else discord.AllowedMentions.none()
    return await canal.send(view=montar_anuncio(dados), files=arquivos, allowed_mentions=mencoes)


def anuncio_agendar(guild_id: int, canal_id: int, autor_id: int, dados: dict, quando: float) -> int:
    with banco.conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO anuncios_agendados (guild_id, canal_id, autor_id, dados, quando) VALUES (?, ?, ?, ?, ?)",
            (guild_id, canal_id, autor_id, json.dumps(dados), quando),
        )
        conexao.commit()
        return cursor.lastrowid


def anuncios_vencidos(agora: float) -> list[tuple[int, int, int, dict]]:
    return [
        (aid, guild_id, canal_id, json.loads(dados))
        for aid, guild_id, canal_id, dados in banco.todos(
            "SELECT id, guild_id, canal_id, dados FROM anuncios_agendados WHERE quando <= ?", (agora,)
        )
    ]


def anuncios_pendentes(guild_id: int) -> list[tuple[int, int, str, float]]:
    return [
        (aid, canal_id, json.loads(dados)["titulo"], quando)
        for aid, canal_id, dados, quando in banco.todos(
            "SELECT id, canal_id, dados, quando FROM anuncios_agendados WHERE guild_id = ? ORDER BY quando", (guild_id,)
        )
    ]


def anuncio_apagar(anuncio_id: int, guild_id: int | None = None) -> dict | None:
    linha = banco.um("SELECT guild_id, dados FROM anuncios_agendados WHERE id = ?", (anuncio_id,))
    if linha is None or (guild_id is not None and linha[0] != guild_id):
        return None
    banco.executar("DELETE FROM anuncios_agendados WHERE id = ?", (anuncio_id,))
    return json.loads(linha[1])


def _caminho_imagem(dados: dict) -> str | None:
    return os.path.join(PASTA_IMAGENS, dados["imagem_arquivo"]) if dados.get("imagem_arquivo") else None


class ModalAnuncio(ModalDoBot, title="Novo anuncio"):
    def __init__(self, canal: discord.abc.GuildChannel, cargo: discord.Role | None, quando: datetime.datetime | None, cor: int | None):
        super().__init__(timeout=1200)
        self.canal = canal
        self.cargo = cargo
        self.quando = quando
        self.cor = cor
        self.titulo = discord.ui.TextInput(max_length=200)
        self.texto = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=3500)
        self.imagem = discord.ui.FileUpload(required=False, max_values=1)
        self.botao = discord.ui.TextInput(required=False, max_length=300, placeholder="Inscreva-se | https://...")
        self.add_item(discord.ui.Label(text="Titulo", component=self.titulo))
        self.add_item(discord.ui.Label(text="Texto", description="Aceita **negrito**, listas e links", component=self.texto))
        self.add_item(discord.ui.Label(text="Imagem (opcional)", component=self.imagem))
        self.add_item(
            discord.ui.Label(text="Botao com link (opcional)", description="Formato: texto do botao | link", component=self.botao)
        )

    async def on_submit(self, interaction: discord.Interaction):
        botao = None
        if self.botao.value.strip():
            botao = interpretar_botao(self.botao.value)
            if botao is None:
                await interaction.response.send_message(
                    "O botao precisa ser `texto | https://link`.", ephemeral=True
                )
                return
        anexo = self.imagem.values[0] if self.imagem.values else None
        if anexo is not None and (not (anexo.content_type or "").startswith("image/") or anexo.size > TAMANHO_MAX_IMAGEM):
            await interaction.response.send_message("A imagem precisa ser um arquivo de imagem de ate 8 MB.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        dados = {
            "titulo": self.titulo.value,
            "texto": self.texto.value,
            "botao": botao,
            "cargo": self.cargo.id if self.cargo else None,
            "cor": self.cor,
        }
        caminho = None
        if anexo is not None:
            # O link de um anexo de formulario expira - guarda o arquivo.
            os.makedirs(PASTA_IMAGENS, exist_ok=True)
            extensao = os.path.splitext(anexo.filename)[1].lower() or ".png"
            dados["imagem"] = f"imagem{extensao}"
            dados["imagem_arquivo"] = f"{interaction.id}{extensao}"
            caminho = _caminho_imagem(dados)
            await anexo.save(caminho)

        if self.quando is None:
            mensagem = await enviar_anuncio(self.canal, dados, caminho)
            if caminho:
                os.remove(caminho)
            await interaction.followup.send(f"Anuncio publicado: {mensagem.jump_url}", ephemeral=True)
            return
        anuncio_id = await asyncio.to_thread(
            anuncio_agendar, interaction.guild.id, self.canal.id, interaction.user.id, dados, self.quando.timestamp()
        )
        await interaction.followup.send(
            f"Anuncio #{anuncio_id} agendado para {discord.utils.format_dt(self.quando, 'f')} "
            f"({discord.utils.format_dt(self.quando, 'R')}) em {self.canal.mention}.",
            ephemeral=True,
        )


# ---------------------------------------------------------------- avisos de evento


def evento_ja_avisado(evento_id: int) -> bool:
    return banco.um("SELECT 1 FROM eventos_avisados WHERE evento_id = ?", (evento_id,)) is not None


def evento_marcar_avisado(evento_id: int) -> None:
    banco.executar("INSERT OR IGNORE INTO eventos_avisados (evento_id) VALUES (?)", (evento_id,))


# ---------------------------------------------------------------- cog


class Anuncios(commands.Cog):
    anuncio = app_commands.Group(
        name="anuncio",
        description="Anuncios no layout novo, na hora ou agendados",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.enviar_agendados.start()
        self.avisar_eventos.start()

    async def cog_unload(self) -> None:
        self.enviar_agendados.cancel()
        self.avisar_eventos.cancel()

    @anuncio.command(name="criar", description="Escreve um anuncio (com imagem e botao) para agora ou para depois")
    @app_commands.describe(
        canal="Onde publicar",
        mencionar="Cargo para notificar (opcional)",
        quando="Para agendar: daqui quanto tempo (2h) ou quando (25/12 20:00). Vazio = agora",
        cor="Cor da barra lateral em hexadecimal, ex: #ff8800 (opcional)",
    )
    async def criar(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        mencionar: discord.Role | None = None,
        quando: str | None = None,
        cor: str | None = None,
    ):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        momento = None
        if quando:
            momento = interpretar_quando(quando)
            if momento is None or momento <= datetime.datetime.now(FUSO):
                await interaction.response.send_message(
                    "Nao entendi quando. Exemplos: `2h`, `amanha 9h`, `25/12 20:00`.", ephemeral=True
                )
                return
        cor_valor = None
        if cor:
            try:
                cor_valor = discord.Color.from_str(cor if cor.startswith("#") else f"#{cor}").value
            except ValueError:
                await interaction.response.send_message("Cor invalida. Use algo como `#ff8800`.", ephemeral=True)
                return
        if mencionar is not None and mencionar.is_default():
            await interaction.response.send_message(
                "Para avisar todo mundo, crie um cargo de avisos - @everyone nao e mencionado por aqui.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ModalAnuncio(canal, mencionar, momento, cor_valor))

    @anuncio.command(name="agendados", description="Lista os anuncios agendados")
    async def agendados(self, interaction: discord.Interaction):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        pendentes = await asyncio.to_thread(anuncios_pendentes, interaction.guild.id)
        if not pendentes:
            await interaction.response.send_message("Nenhum anuncio agendado.", ephemeral=True)
            return
        linhas = [
            f"`#{aid}` {discord.utils.format_dt(datetime.datetime.fromtimestamp(quando, FUSO), 'f')} em <#{canal_id}> - {titulo[:80]}"
            for aid, canal_id, titulo, quando in pendentes
        ]
        await interaction.response.send_message(
            view=layout.view_estatica(layout.texto("## Anuncios agendados\n" + "\n".join(linhas))), ephemeral=True
        )

    @anuncio.command(name="cancelar", description="Cancela um anuncio agendado")
    @app_commands.describe(anuncio="Qual anuncio")
    async def cancelar(self, interaction: discord.Interaction, anuncio: int):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        dados = await asyncio.to_thread(anuncio_apagar, anuncio, interaction.guild.id)
        if dados is None:
            await interaction.response.send_message("Nao achei esse anuncio.", ephemeral=True)
            return
        caminho = _caminho_imagem(dados)
        if caminho and os.path.exists(caminho):
            os.remove(caminho)
        await interaction.response.send_message(f"Anuncio **{dados['titulo']}** cancelado.", ephemeral=True)

    @cancelar.autocomplete("anuncio")
    async def _cancelar_autocomplete(self, interaction: discord.Interaction, atual: str):
        pendentes = await asyncio.to_thread(anuncios_pendentes, interaction.guild_id)
        return [
            app_commands.Choice(name=f"#{aid} - {titulo}"[:100], value=aid)
            for aid, _, titulo, _ in pendentes
            if atual.lower() in titulo.lower()
        ][:25]

    @tasks.loop(seconds=30)
    async def enviar_agendados(self):
        for anuncio_id, guild_id, canal_id, dados in await asyncio.to_thread(anuncios_vencidos, time.time()):
            await asyncio.to_thread(anuncio_apagar, anuncio_id)
            canal = self.bot.get_channel(canal_id)
            caminho = _caminho_imagem(dados)
            try:
                if canal is not None:
                    await enviar_anuncio(canal, dados, caminho if caminho and os.path.exists(caminho) else None)
            except discord.HTTPException as erro:
                logger.warning(f"Nao consegui publicar o anuncio agendado {anuncio_id}: {erro}")
            finally:
                if caminho and os.path.exists(caminho):
                    os.remove(caminho)

    # ---- avisos de evento

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(self, evento: discord.ScheduledEvent, usuario: discord.User):
        await self._cargo_interessado(evento, usuario, dar=True)

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(self, evento: discord.ScheduledEvent, usuario: discord.User):
        await self._cargo_interessado(evento, usuario, dar=False)

    async def _cargo_interessado(self, evento: discord.ScheduledEvent, usuario: discord.abc.User, dar: bool) -> None:
        config = await asyncio.to_thread(configstore.obter, evento.guild.id, CHAVE_EVENTOS)
        cargo = evento.guild.get_role((config or {}).get("cargo") or 0)
        if cargo is None or cargo >= evento.guild.me.top_role:
            return
        membro = evento.guild.get_member(usuario.id)
        if membro is None:
            return
        try:
            if dar:
                await membro.add_roles(cargo, reason=f"Interessado no evento {evento.name}")
            else:
                await membro.remove_roles(cargo, reason=f"Desistiu do evento {evento.name}")
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui mexer no cargo de interessados em {evento.guild.id}: {erro}")

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, antes: discord.ScheduledEvent, depois: discord.ScheduledEvent):
        # Evento acabou: tira o cargo de interessado de todo mundo.
        if depois.status not in (discord.EventStatus.completed, discord.EventStatus.cancelled):
            return
        config = await asyncio.to_thread(configstore.obter, depois.guild.id, CHAVE_EVENTOS)
        cargo = depois.guild.get_role((config or {}).get("cargo") or 0)
        if cargo is None:
            return
        async for usuario in depois.users():
            await self._cargo_interessado(depois, usuario, dar=False)

    @tasks.loop(minutes=1)
    async def avisar_eventos(self):
        agora = discord.utils.utcnow()
        for guild_id, config in await asyncio.to_thread(configstore.servidores_com, CHAVE_EVENTOS):
            guild = self.bot.get_guild(guild_id)
            canal = guild.get_channel(config.get("canal") or 0) if guild else None
            if canal is None:
                continue
            antecedencia = datetime.timedelta(minutes=config.get("minutos", 15))
            for evento in guild.scheduled_events:
                if evento.status is not discord.EventStatus.scheduled:
                    continue
                if not agora <= evento.start_time <= agora + antecedencia:
                    continue
                if await asyncio.to_thread(evento_ja_avisado, evento.id):
                    continue
                await asyncio.to_thread(evento_marcar_avisado, evento.id)
                await self._avisar(evento, canal, config)

    async def _avisar(self, evento: discord.ScheduledEvent, canal: discord.TextChannel, config: dict) -> None:
        cargo = evento.guild.get_role(config.get("cargo") or 0)
        if cargo is not None:
            quem, mencoes = cargo.mention, discord.AllowedMentions(roles=[cargo])
        else:
            interessados = [usuario async for usuario in evento.users(limit=50)]
            quem = " ".join(u.mention for u in interessados) or "Pessoal"
            mencoes = discord.AllowedMentions(users=True)
        local = evento.channel.mention if evento.channel else (evento.location or "")
        try:
            await canal.send(
                f"⏰ {quem}, **{evento.name}** comeca {discord.utils.format_dt(evento.start_time, 'R')}"
                + (f" em {local}" if local else "")
                + f"!\n{evento.url}",
                allowed_mentions=mencoes,
            )
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui avisar o evento {evento.id}: {erro}")

    @enviar_agendados.before_loop
    @avisar_eventos.before_loop
    async def _antes(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Anuncios(bot))
