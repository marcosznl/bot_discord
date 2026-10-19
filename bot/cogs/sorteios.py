import asyncio
import datetime
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import banco, layout
from bot.cogs.comunidade import interpretar_quando
from bot.config import FUSO
from bot.permissions import requer_permissao

logger = logging.getLogger(__name__)

SORTEIO_MAX_SEGUNDOS = 60 * 24 * 60 * 60

banco.registrar_schema(
    """
    CREATE TABLE IF NOT EXISTS sorteios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        canal_id INTEGER NOT NULL,
        mensagem_id INTEGER,
        criador_id INTEGER NOT NULL,
        premio TEXT NOT NULL,
        vencedores INTEGER NOT NULL,
        cargo_requerido INTEGER,
        termina_em REAL NOT NULL,
        encerrado INTEGER NOT NULL DEFAULT 0,
        ganhadores TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sorteio_participantes (
        sorteio_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (sorteio_id, user_id)
    );
    """
)


# ---------------------------------------------------------------- banco


def sorteio_criar(
    guild_id: int, canal_id: int, criador_id: int, premio: str, vencedores: int, cargo_requerido: int | None, termina_em: float
) -> int:
    with banco.conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO sorteios (guild_id, canal_id, criador_id, premio, vencedores, cargo_requerido, termina_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, canal_id, criador_id, premio, vencedores, cargo_requerido, termina_em),
        )
        conexao.commit()
        return cursor.lastrowid


def sorteio_definir_mensagem(sorteio_id: int, mensagem_id: int) -> None:
    banco.executar("UPDATE sorteios SET mensagem_id = ? WHERE id = ?", (mensagem_id, sorteio_id))


def sorteio_obter(sorteio_id: int) -> dict | None:
    linha = banco.um(
        "SELECT id, guild_id, canal_id, mensagem_id, criador_id, premio, vencedores, cargo_requerido, termina_em, "
        "encerrado, ganhadores FROM sorteios WHERE id = ?",
        (sorteio_id,),
    )
    if linha is None:
        return None
    chaves = (
        "id", "guild_id", "canal_id", "mensagem_id", "criador_id", "premio", "vencedores", "cargo_requerido",
        "termina_em", "encerrado", "ganhadores",
    )
    sorteio = dict(zip(chaves, linha))
    sorteio["ganhadores"] = [int(uid) for uid in sorteio["ganhadores"].split(",") if uid]
    sorteio["participantes"] = banco.um(
        "SELECT COUNT(*) FROM sorteio_participantes WHERE sorteio_id = ?", (sorteio_id,)
    )[0]
    return sorteio


def sorteio_alternar_participacao(sorteio_id: int, user_id: int) -> bool:
    """Entra ou sai do sorteio. Devolve True se agora esta participando."""
    removido = banco.executar_rowcount(
        "DELETE FROM sorteio_participantes WHERE sorteio_id = ? AND user_id = ?", (sorteio_id, user_id)
    )
    if removido:
        return False
    banco.executar("INSERT INTO sorteio_participantes (sorteio_id, user_id) VALUES (?, ?)", (sorteio_id, user_id))
    return True


def sorteio_sortear(sorteio_id: int, quantidade: int, excluir: set[int] = frozenset()) -> list[int]:
    participantes = [
        uid
        for (uid,) in banco.todos("SELECT user_id FROM sorteio_participantes WHERE sorteio_id = ?", (sorteio_id,))
        if uid not in excluir
    ]
    return random.sample(participantes, min(quantidade, len(participantes)))


def sorteio_encerrar(sorteio_id: int, ganhadores: list[int]) -> None:
    banco.executar(
        "UPDATE sorteios SET encerrado = 1, ganhadores = ? WHERE id = ?",
        (",".join(str(uid) for uid in ganhadores), sorteio_id),
    )


def sorteios_vencidos(agora: float) -> list[int]:
    return [sid for (sid,) in banco.todos("SELECT id FROM sorteios WHERE encerrado = 0 AND termina_em <= ?", (agora,))]


def sorteios_do_servidor(guild_id: int, encerrado: bool) -> list[tuple[int, str]]:
    return banco.todos(
        "SELECT id, premio FROM sorteios WHERE guild_id = ? AND encerrado = ? ORDER BY id DESC LIMIT 25",
        (guild_id, int(encerrado)),
    )


# ---------------------------------------------------------------- ui


class BotaoParticipar(discord.ui.DynamicItem[discord.ui.Button], template=r"sorteio:(?P<id>\d+)"):
    def __init__(self, sorteio_id: int, desativado: bool = False):
        super().__init__(
            discord.ui.Button(
                label="Participar",
                emoji="🎉",
                style=discord.ButtonStyle.success,
                custom_id=f"sorteio:{sorteio_id}",
                disabled=desativado,
            )
        )
        self.sorteio_id = sorteio_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        sorteio = await asyncio.to_thread(sorteio_obter, self.sorteio_id)
        if sorteio is None or sorteio["encerrado"]:
            await interaction.response.send_message("Esse sorteio ja acabou.", ephemeral=True)
            return
        cargo_id = sorteio["cargo_requerido"]
        if cargo_id and not any(cargo.id == cargo_id for cargo in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message(
                f"So quem tem o cargo <@&{cargo_id}> pode participar.", ephemeral=True
            )
            return

        participando = await asyncio.to_thread(sorteio_alternar_participacao, self.sorteio_id, interaction.user.id)
        sorteio["participantes"] += 1 if participando else -1
        await interaction.response.edit_message(view=painel_sorteio(sorteio), allowed_mentions=discord.AllowedMentions.none())
        texto = "Voce esta participando! Clique de novo para sair." if participando else "Voce saiu do sorteio."
        await interaction.followup.send(texto, ephemeral=True)


def painel_sorteio(sorteio: dict) -> discord.ui.LayoutView:
    termina = datetime.datetime.fromtimestamp(sorteio["termina_em"], FUSO)
    linhas = [f"## 🎉 {sorteio['premio']}"]
    if sorteio["encerrado"]:
        if sorteio["ganhadores"]:
            linhas.append("**Ganhador(es):** " + ", ".join(f"<@{uid}>" for uid in sorteio["ganhadores"]))
        else:
            linhas.append("Ninguem participou.")
        linhas.append(f"Encerrado {discord.utils.format_dt(termina, 'R')}")
    else:
        linhas.append(f"Termina {discord.utils.format_dt(termina, 'R')} ({discord.utils.format_dt(termina, 'f')})")
        linhas.append(f"**Vencedores:** {sorteio['vencedores']}")
        if sorteio["cargo_requerido"]:
            linhas.append(f"**So para:** <@&{sorteio['cargo_requerido']}>")
    linhas.append(f"-# {sorteio['participantes']} participante(s) | criado por <@{sorteio['criador_id']}> | #{sorteio['id']}")

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            layout.texto("\n".join(linhas)),
            layout.linha(BotaoParticipar(sorteio["id"], desativado=bool(sorteio["encerrado"]))),
            accent_color=discord.Color.dark_grey() if sorteio["encerrado"] else discord.Color.gold(),
        )
    )
    return view


# ---------------------------------------------------------------- cog


class Sorteios(commands.Cog):
    sorteio = app_commands.Group(
        name="sorteio",
        description="Sorteios com botao de participar",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.encerrar_vencidos.start()

    async def cog_unload(self) -> None:
        self.encerrar_vencidos.cancel()

    async def _finalizar(self, sorteio_id: int, ressortear: bool = False) -> list[int] | None:
        sorteio = await asyncio.to_thread(sorteio_obter, sorteio_id)
        if sorteio is None:
            return None
        excluir = set(sorteio["ganhadores"]) if ressortear else set()
        quantidade = 1 if ressortear else sorteio["vencedores"]
        novos = await asyncio.to_thread(sorteio_sortear, sorteio_id, quantidade, excluir)
        ganhadores = (sorteio["ganhadores"] + novos) if ressortear else novos
        await asyncio.to_thread(sorteio_encerrar, sorteio_id, ganhadores)
        sorteio.update(encerrado=1, ganhadores=ganhadores)

        canal = self.bot.get_channel(sorteio["canal_id"])
        if canal is None:
            return novos
        try:
            if sorteio["mensagem_id"]:
                mensagem = canal.get_partial_message(sorteio["mensagem_id"])
                await mensagem.edit(view=painel_sorteio(sorteio), allowed_mentions=discord.AllowedMentions.none())
            if novos:
                mencoes = ", ".join(f"<@{uid}>" for uid in novos)
                frase = "Novo sorteio" if ressortear else "Parabens"
                await canal.send(
                    f"🎉 {frase}, {mencoes}! Voce(s) ganhou(aram) **{sorteio['premio']}**.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            elif not ressortear:
                await canal.send(f"O sorteio de **{sorteio['premio']}** acabou sem participantes.")
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui anunciar o sorteio {sorteio_id}: {erro}")
        return novos

    @sorteio.command(name="criar", description="Cria um sorteio neste canal")
    @app_commands.describe(
        premio="O que vai ser sorteado",
        duracao="Quanto tempo fica aberto (ex: 30m, 2h, 3d) ou ate quando (25/12 20:00)",
        vencedores="Quantas pessoas ganham",
        cargo_requerido="So quem tem esse cargo pode participar (opcional)",
    )
    async def criar(
        self,
        interaction: discord.Interaction,
        premio: app_commands.Range[str, 1, 200],
        duracao: str,
        vencedores: app_commands.Range[int, 1, 20] = 1,
        cargo_requerido: discord.Role | None = None,
    ):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        agora = datetime.datetime.now(FUSO)
        termina = interpretar_quando(duracao, agora)
        if termina is None or termina <= agora:
            await interaction.response.send_message(
                "Nao entendi a duracao. Exemplos: `30m`, `2h`, `3d`, `25/12 20:00`.", ephemeral=True
            )
            return
        if (termina - agora).total_seconds() > SORTEIO_MAX_SEGUNDOS:
            await interaction.response.send_message("O limite e de 60 dias.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        sorteio_id = await asyncio.to_thread(
            sorteio_criar,
            interaction.guild.id,
            interaction.channel_id,
            interaction.user.id,
            premio,
            vencedores,
            cargo_requerido.id if cargo_requerido else None,
            termina.timestamp(),
        )
        sorteio = await asyncio.to_thread(sorteio_obter, sorteio_id)
        mensagem = await interaction.channel.send(view=painel_sorteio(sorteio), allowed_mentions=discord.AllowedMentions.none())
        await asyncio.to_thread(sorteio_definir_mensagem, sorteio_id, mensagem.id)
        await interaction.followup.send(f"Sorteio #{sorteio_id} criado.", ephemeral=True)

    @sorteio.command(name="encerrar", description="Encerra um sorteio agora e sorteia os ganhadores")
    @app_commands.describe(sorteio="Qual sorteio")
    async def encerrar(self, interaction: discord.Interaction, sorteio: int):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        dados = await asyncio.to_thread(sorteio_obter, sorteio)
        if dados is None or dados["guild_id"] != interaction.guild.id or dados["encerrado"]:
            await interaction.response.send_message("Nao achei esse sorteio aberto neste servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._finalizar(sorteio)
        await interaction.followup.send("Sorteio encerrado.", ephemeral=True)

    @sorteio.command(name="ressortear", description="Sorteia mais um ganhador de um sorteio ja encerrado")
    @app_commands.describe(sorteio="Qual sorteio")
    async def ressortear(self, interaction: discord.Interaction, sorteio: int):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        dados = await asyncio.to_thread(sorteio_obter, sorteio)
        if dados is None or dados["guild_id"] != interaction.guild.id or not dados["encerrado"]:
            await interaction.response.send_message("Nao achei esse sorteio encerrado neste servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        novos = await self._finalizar(sorteio, ressortear=True)
        texto = "Novo ganhador sorteado." if novos else "Nao sobrou ninguem para sortear."
        await interaction.followup.send(texto, ephemeral=True)

    async def _autocomplete(self, interaction: discord.Interaction, atual: str, encerrado: bool):
        sorteios = await asyncio.to_thread(sorteios_do_servidor, interaction.guild_id, encerrado)
        return [
            app_commands.Choice(name=f"#{sid} - {premio}"[:100], value=sid)
            for sid, premio in sorteios
            if atual.lower() in premio.lower() or atual == str(sid)
        ]

    @encerrar.autocomplete("sorteio")
    async def _autocomplete_abertos(self, interaction: discord.Interaction, atual: str):
        return await self._autocomplete(interaction, atual, encerrado=False)

    @ressortear.autocomplete("sorteio")
    async def _autocomplete_encerrados(self, interaction: discord.Interaction, atual: str):
        return await self._autocomplete(interaction, atual, encerrado=True)

    @tasks.loop(seconds=15)
    async def encerrar_vencidos(self):
        for sorteio_id in await asyncio.to_thread(sorteios_vencidos, time.time()):
            try:
                await self._finalizar(sorteio_id)
            except Exception:
                # Marca como encerrado mesmo assim, senao o loop tentaria de
                # novo a cada 15s para sempre.
                logger.exception(f"Erro ao encerrar o sorteio {sorteio_id}")
                await asyncio.to_thread(sorteio_encerrar, sorteio_id, [])

    @encerrar_vencidos.before_loop
    async def _antes(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoParticipar)
    await bot.add_cog(Sorteios(bot))
