import asyncio
import datetime
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import banco, configstore, layout
from bot.config import FUSO

logger = logging.getLogger(__name__)

# {"canal": id, "cargo": id ou None}
CHAVE_CONFIG = "aniversarios"
HORA_DO_PARABENS = 9

banco.registrar_schema(
    """
    CREATE TABLE IF NOT EXISTS aniversarios (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        dia INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        ano_anunciado INTEGER NOT NULL DEFAULT 0,
        cargo_dado_em TEXT,
        PRIMARY KEY (guild_id, user_id)
    );
    """
)

MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def interpretar_dia_mes(texto: str) -> tuple[int, int] | None:
    casamento = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{1,2})\s*", texto)
    if casamento is None:
        return None
    dia, mes = int(casamento[1]), int(casamento[2])
    try:
        datetime.date(2024, mes, dia)  # ano bissexto: aceita 29/02
    except ValueError:
        return None
    return dia, mes


def comemora_hoje(dia: int, mes: int, hoje: datetime.date) -> bool:
    if (dia, mes) == (hoje.day, hoje.month):
        return True
    # Quem nasceu em 29/02 comemora em 28/02 nos anos nao bissextos.
    bissexto = hoje.year % 4 == 0 and (hoje.year % 100 != 0 or hoje.year % 400 == 0)
    return (dia, mes) == (29, 2) and not bissexto and (hoje.day, hoje.month) == (28, 2)


def dias_ate(dia: int, mes: int, hoje: datetime.date) -> int:
    for ano in (hoje.year, hoje.year + 1, hoje.year + 2):
        try:
            data = datetime.date(ano, mes, dia)
        except ValueError:
            data = datetime.date(ano, 2, 28)
        if data >= hoje:
            return (data - hoje).days
    return 366


def aniversario_definir(guild_id: int, user_id: int, dia: int, mes: int) -> None:
    banco.executar(
        "INSERT INTO aniversarios (guild_id, user_id, dia, mes) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (guild_id, user_id) DO UPDATE SET dia = excluded.dia, mes = excluded.mes",
        (guild_id, user_id, dia, mes),
    )


def aniversario_remover(guild_id: int, user_id: int) -> bool:
    return banco.executar_rowcount(
        "DELETE FROM aniversarios WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ) > 0


def aniversarios_do_servidor(guild_id: int) -> list[tuple[int, int, int, int, str | None]]:
    return banco.todos(
        "SELECT user_id, dia, mes, ano_anunciado, cargo_dado_em FROM aniversarios WHERE guild_id = ?", (guild_id,)
    )


def aniversario_marcar_anunciado(guild_id: int, user_id: int, ano: int, cargo_dado_em: str | None) -> None:
    banco.executar(
        "UPDATE aniversarios SET ano_anunciado = ?, cargo_dado_em = ? WHERE guild_id = ? AND user_id = ?",
        (ano, cargo_dado_em, guild_id, user_id),
    )


def aniversario_cargo_retirado(guild_id: int, user_id: int) -> None:
    banco.executar(
        "UPDATE aniversarios SET cargo_dado_em = NULL WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )


class Aniversarios(commands.Cog):
    aniversario = app_commands.Group(name="aniversario", description="Aniversarios do servidor", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.verificar.start()

    async def cog_unload(self) -> None:
        self.verificar.cancel()

    @aniversario.command(name="definir", description="Cadastra o seu aniversario")
    @app_commands.describe(data="Dia e mes, ex: 25/12")
    async def definir(self, interaction: discord.Interaction, data: str):
        dia_mes = interpretar_dia_mes(data)
        if dia_mes is None:
            await interaction.response.send_message("Use o formato dia/mes, ex: `25/12`.", ephemeral=True)
            return
        dia, mes = dia_mes
        await asyncio.to_thread(aniversario_definir, interaction.guild.id, interaction.user.id, dia, mes)
        await interaction.response.send_message(f"Anotado: {dia} de {MESES[mes - 1]}. 🎂", ephemeral=True)

    @aniversario.command(name="remover", description="Tira o seu aniversario da lista")
    async def remover(self, interaction: discord.Interaction):
        removido = await asyncio.to_thread(aniversario_remover, interaction.guild.id, interaction.user.id)
        texto = "Aniversario removido." if removido else "Voce nao tinha aniversario cadastrado."
        await interaction.response.send_message(texto, ephemeral=True)

    @aniversario.command(name="proximos", description="Mostra os proximos aniversarios do servidor")
    async def proximos(self, interaction: discord.Interaction):
        hoje = datetime.datetime.now(FUSO).date()
        todos = await asyncio.to_thread(aniversarios_do_servidor, interaction.guild.id)
        if not todos:
            await interaction.response.send_message(
                "Ninguem cadastrou aniversario ainda. Use `/aniversario definir`.", ephemeral=True
            )
            return
        ordenados = sorted(todos, key=lambda linha: dias_ate(linha[1], linha[2], hoje))[:15]
        linhas = []
        for user_id, dia, mes, _, _ in ordenados:
            faltam = dias_ate(dia, mes, hoje)
            quando = "**hoje!** 🎉" if faltam == 0 else ("amanha" if faltam == 1 else f"em {faltam} dias")
            linhas.append(f"<@{user_id}> - {dia:02d}/{mes:02d} ({quando})")
        view = layout.view_estatica(
            layout.texto("## 🎂 Proximos aniversarios\n" + "\n".join(linhas)), cor=discord.Color.pink()
        )
        await interaction.response.send_message(view=view, allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=30)
    async def verificar(self):
        agora = datetime.datetime.now(FUSO)
        hoje = agora.date()
        for guild_id, config in await asyncio.to_thread(configstore.servidores_com, CHAVE_CONFIG):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            canal = guild.get_channel(config.get("canal") or 0)
            cargo = guild.get_role(config.get("cargo") or 0)
            for user_id, dia, mes, ano_anunciado, cargo_dado_em in await asyncio.to_thread(
                aniversarios_do_servidor, guild_id
            ):
                try:
                    await self._processar(guild, canal, cargo, user_id, dia, mes, ano_anunciado, cargo_dado_em, agora, hoje)
                except discord.HTTPException as erro:
                    logger.warning(f"Erro no aniversario de {user_id} em {guild_id}: {erro}")

    async def _processar(self, guild, canal, cargo, user_id, dia, mes, ano_anunciado, cargo_dado_em, agora, hoje):
        # Cargo de aniversariante dura so o dia.
        if cargo_dado_em and cargo_dado_em != hoje.isoformat():
            try:
                membro = guild.get_member(user_id) or await guild.fetch_member(user_id)
            except discord.NotFound:
                membro = None  # saiu do servidor - o cargo saiu junto
            if membro is not None and cargo is not None and cargo in membro.roles:
                await membro.remove_roles(cargo, reason="Fim do dia de aniversario")
            await asyncio.to_thread(aniversario_cargo_retirado, guild.id, user_id)

        if ano_anunciado == hoje.year or agora.hour < HORA_DO_PARABENS or not comemora_hoje(dia, mes, hoje):
            return
        try:
            membro = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            # Saiu do servidor - nao anuncia, mas marca para nao tentar de novo hoje.
            await asyncio.to_thread(aniversario_marcar_anunciado, guild.id, user_id, hoje.year, None)
            return

        dado_em = None
        if cargo is not None and cargo < guild.me.top_role:
            await membro.add_roles(cargo, reason="Aniversario")
            dado_em = hoje.isoformat()
        await asyncio.to_thread(aniversario_marcar_anunciado, guild.id, user_id, hoje.year, dado_em)
        if canal is not None:
            await canal.send(
                f"🎂 Hoje e aniversario de {membro.mention}! Parabens! 🎉",
                allowed_mentions=discord.AllowedMentions(users=True),
            )

    @verificar.before_loop
    async def _antes(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Aniversarios(bot))
