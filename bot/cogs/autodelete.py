import asyncio
import logging
import os
import sqlite3
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import sqlitestore
from bot.config import DATA_DIR

logger = logging.getLogger(__name__)

AUTODELETE_DB_PATH = os.path.join(DATA_DIR, "autodelete.db")
# Minimo de 1 minuto - evita configurar um intervalo tao curto que o bot fique
# tentando limpar o canal sem parar, gastando chamadas a API a toa.
AUTODELETE_SEGUNDOS_MINIMO = 60


def _autodelete_conectar_bd() -> sqlite3.Connection:
    conexao = sqlite3.connect(AUTODELETE_DB_PATH)
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS autodelete_canais ("
        "channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, segundos INTEGER NOT NULL)"
    )
    return conexao


def _autodelete_definir(channel_id: int, guild_id: int, segundos: int) -> None:
    sqlitestore.executar(
        _autodelete_conectar_bd,
        "INSERT INTO autodelete_canais (channel_id, guild_id, segundos) VALUES (?, ?, ?) "
        "ON CONFLICT (channel_id) DO UPDATE SET segundos = excluded.segundos",
        (channel_id, guild_id, segundos),
    )


def _autodelete_remover(channel_id: int) -> bool:
    return (
        sqlitestore.executar_retornando_rowcount(
            _autodelete_conectar_bd, "DELETE FROM autodelete_canais WHERE channel_id = ?", (channel_id,)
        )
        > 0
    )


def _autodelete_do_canal(channel_id: int) -> int | None:
    linha = sqlitestore.consultar_um(
        _autodelete_conectar_bd, "SELECT segundos FROM autodelete_canais WHERE channel_id = ?", (channel_id,)
    )
    return linha[0] if linha else None


def _autodelete_listar_servidor(guild_id: int) -> list[tuple[int, int]]:
    return sqlitestore.consultar_todos(
        _autodelete_conectar_bd,
        "SELECT channel_id, segundos FROM autodelete_canais WHERE guild_id = ?",
        (guild_id,),
    )


def _autodelete_listar_todos() -> list[tuple[int, int]]:
    return sqlitestore.consultar_todos(_autodelete_conectar_bd, "SELECT channel_id, segundos FROM autodelete_canais")


def _formatar_intervalo(segundos: int) -> str:
    if segundos % 86400 == 0 and segundos >= 86400:
        return f"{segundos // 86400}d"
    if segundos % 3600 == 0 and segundos >= 3600:
        return f"{segundos // 3600}h"
    if segundos % 60 == 0:
        return f"{segundos // 60}min"
    return f"{segundos}s"


class ModalAutodeleteAtivar(discord.ui.Modal, title="Auto-apagar mensagens"):
    minutos = discord.ui.TextInput(
        label="Apagar mensagens com mais de (minutos)", placeholder="60", max_length=6
    )

    def __init__(self, channel_id: int, guild_id: int):
        super().__init__()
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.minutos).strip()
        if not texto.isdigit() or int(texto) <= 0:
            await interaction.response.send_message(
                "Informe um numero inteiro de minutos maior que 0.", ephemeral=True
            )
            return

        segundos = int(texto) * 60
        if segundos < AUTODELETE_SEGUNDOS_MINIMO:
            await interaction.response.send_message(
                f"O minimo e {AUTODELETE_SEGUNDOS_MINIMO // 60} minuto(s), pra nao sobrecarregar "
                "o Discord com pedidos de limpeza repetidos demais.",
                ephemeral=True,
            )
            return

        await asyncio.to_thread(_autodelete_definir, self.channel_id, self.guild_id, segundos)
        await interaction.response.send_message(
            f"Auto-apagar ativado neste canal: mensagens com mais de {_formatar_intervalo(segundos)} "
            "serao apagadas automaticamente.",
            ephemeral=True,
        )


class BotoesAutodelete(discord.ui.View):
    def __init__(self, channel_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.guild_id = guild_id

    @discord.ui.button(label="Ativar neste canal", style=discord.ButtonStyle.success)
    async def ativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalAutodeleteAtivar(self.channel_id, self.guild_id))

    @discord.ui.button(label="Desativar neste canal", style=discord.ButtonStyle.danger)
    async def desativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        removido = await asyncio.to_thread(_autodelete_remover, self.channel_id)
        if removido:
            await interaction.response.send_message("Auto-apagar desativado neste canal.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Este canal nao tinha auto-apagar ativado.", ephemeral=True
            )

    @discord.ui.button(label="Ver configurados", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, botao: discord.ui.Button):
        canais = await asyncio.to_thread(_autodelete_listar_servidor, self.guild_id)
        if not canais:
            await interaction.response.send_message(
                "Nenhum canal com auto-apagar configurado neste servidor.", ephemeral=True
            )
            return

        linhas = [
            f"<#{channel_id}>: mensagens com mais de {_formatar_intervalo(segundos)}"
            for channel_id, segundos in canais
        ]
        await interaction.response.send_message("\n".join(linhas), ephemeral=True)


class AutoDelete(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.limpar_canais_configurados.cancel()

    @tasks.loop(seconds=30)
    async def limpar_canais_configurados(self):
        canais = await asyncio.to_thread(_autodelete_listar_todos)
        if not canais:
            return

        agora = discord.utils.utcnow()
        for channel_id, segundos in canais:
            canal = self.bot.get_channel(channel_id)
            if canal is None:
                continue

            cutoff = agora - timedelta(seconds=segundos)
            try:
                await canal.purge(limit=200, before=cutoff)
            except discord.Forbidden:
                logger.warning(f"Sem permissao para apagar mensagens no canal {channel_id} (autodelete).")
            except discord.HTTPException as erro:
                logger.error(f"Erro ao limpar canal {channel_id} (autodelete): {erro}")

    @app_commands.command(name="autodelete", description="Configura apagar mensagens automaticamente neste canal")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def autodelete(self, interaction: discord.Interaction):
        atual = await asyncio.to_thread(_autodelete_do_canal, interaction.channel.id)
        status = (
            f"Auto-apagar ativo neste canal: mensagens com mais de {_formatar_intervalo(atual)} sao apagadas."
            if atual
            else "Auto-apagar nao esta ativo neste canal."
        )
        await interaction.response.send_message(
            status, view=BotoesAutodelete(interaction.channel.id, interaction.guild.id), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoDelete(bot))
