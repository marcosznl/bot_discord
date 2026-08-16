import asyncio
import os
import platform

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from bot.cogs import llm as llm_cog
from bot.cogs.minecraft import (
    _mc_jogadores_online,
    _mc_ram_mb,
    _mc_servers_atual,
    _mc_servidor_pronto,
    _mc_tempo_ligado,
)
from bot.config import MC_SERVERS_DIR

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "status", "ajuda"],
    "Moderacao": ["limpar", "boasvindas"],
    "Niveis": ["xp"],
    "Diversao": ["gif", "socao", "abraco", "charme"],
    "Musica": ["musica", "playlist"],
    "Minecraft": ["mc"],
    "LLM": ["llm"],
    "Administracao (dono)": ["desligar", "reiniciar", "velocidade_server"],
}


def _todos_comandos_registrados(bot: commands.Bot) -> dict[str, app_commands.Command]:
    resultado = {}
    for item in bot.tree.get_commands():
        if isinstance(item, app_commands.Group):
            for sub in item.commands:
                resultado[f"{item.name} {sub.name}"] = sub
        elif isinstance(item, app_commands.Command):
            resultado[item.name] = item
    return resultado


async def _status_acao_maquina(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
    ram = psutil.virtual_memory()
    if platform.system() == "Windows":
        disk_path = os.getenv("SystemDrive", "C:") + "\\"
    elif platform.system() == "Darwin" and os.path.exists("/System/Volumes/Data"):
        disk_path = "/System/Volumes/Data"
    else:
        disk_path = "/"
    disk = psutil.disk_usage(disk_path)

    ram_bot_mb = psutil.Process(os.getpid()).memory_info().rss // (1024**2)
    embed = discord.Embed(title="Monitoramento do servidor", color=discord.Color.blue())
    topicos = ["CPU", "RAM", "RAM do bot", "Disco"]
    valores = [
        f"{cpu}%",
        f"{ram.percent}% ({(ram.total - ram.available) // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        f"{ram_bot_mb}MB",
        f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
    ]
    embed.add_field(name="Recurso", value="\n".join(topicos), inline=True)
    embed.add_field(name="Valor", value="\n".join(valores), inline=True)
    await interaction.followup.send(embed=embed)


async def _status_acao_minecraft(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    servidores = await asyncio.to_thread(_mc_servers_atual)

    embed_servidores = discord.Embed(title="Servidores de Minecraft", color=discord.Color.green())
    for config in servidores.values():
        tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
        if tempo is None:
            embed_servidores.add_field(name=config["nome"], value="Desligado", inline=False)
            continue

        pronto = await asyncio.to_thread(_mc_servidor_pronto, config)
        linhas = [f"**Status:** {'Ligado ha ' + tempo if pronto else f'Ligando... ({tempo})'}"]

        ram_mb = await asyncio.to_thread(_mc_ram_mb, config)
        if ram_mb is not None:
            linhas.append(f"**RAM:** {ram_mb}MB")

        if pronto:
            jogadores = _mc_jogadores_online(config)
            if jogadores:
                linhas.append(f"**Jogadores:** {jogadores}")

        embed_servidores.add_field(name=config["nome"], value="\n".join(linhas), inline=False)

    if not servidores:
        embed_servidores.description = "Nenhum servidor encontrado em " + MC_SERVERS_DIR

    await interaction.followup.send(embed=embed_servidores)


async def _status_acao_llm(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    embed_llm = discord.Embed(title="Inteligencia Artificial", color=discord.Color.purple())
    if await asyncio.to_thread(llm_cog._llm_servidor_rodando):
        ram_mb = await asyncio.to_thread(llm_cog._llm_ram_mb)
        parametros = await asyncio.to_thread(llm_cog._llm_parametros, llm_cog.LLM_MODELO)
        finalidade = llm_cog.LLM_DESCRICOES.get(llm_cog.LLM_MODELO, "Modelo geral.")

        topicos_llm = ["Status", "Modelo"]
        valores_llm = ["Ligada", llm_cog.LLM_MODELO]
        if ram_mb is not None:
            topicos_llm.append("RAM")
            valores_llm.append(f"{ram_mb}MB")
        topicos_llm.append("Parametros")
        valores_llm.append(parametros or "?")
        topicos_llm.append("Finalidade")
        valores_llm.append(finalidade)
    else:
        topicos_llm = ["Status"]
        valores_llm = ["Desligada"]
    embed_llm.add_field(name="Info", value="\n".join(topicos_llm), inline=True)
    embed_llm.add_field(name="Valor", value="\n".join(valores_llm), inline=True)
    await interaction.followup.send(embed=embed_llm)


class BotoesStatus(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Servidor", style=discord.ButtonStyle.primary)
    async def maquina(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_maquina(interaction)

    @discord.ui.button(label="Minecraft", style=discord.ButtonStyle.success)
    async def minecraft(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_minecraft(interaction)

    @discord.ui.button(label="LLM", style=discord.ButtonStyle.secondary)
    async def llm(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_llm(interaction)


class Geral(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Testa se o bot esta online")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("Pong!")

    @app_commands.command(name="limpar", description="Apaga mensagens recentes deste canal")
    @app_commands.describe(quantidade="Quantas mensagens apagar (1 a 200)")
    @app_commands.default_permissions(manage_messages=True)
    async def limpar(self, interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 200]):
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.purge(limit=quantidade)
        await interaction.delete_original_response()

    @app_commands.command(name="ajuda", description="Mostra todos os comandos do bot, por categoria")
    async def ajuda(self, interaction: discord.Interaction):
        comandos_por_nome = _todos_comandos_registrados(self.bot)

        embed = discord.Embed(title="Comandos disponiveis", color=discord.Color.blue())
        for categoria, nomes in COMANDOS_CATEGORIAS.items():
            linhas = []
            for nome in nomes:
                comando = comandos_por_nome.get(nome)
                if comando:
                    linhas.append(f"`/{nome}` - {comando.description}")
            if linhas:
                embed.add_field(name=categoria, value="\n".join(linhas), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Mostra o status da maquina, servidores de Minecraft e LLM")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.send_message("Qual status voce quer ver?", view=BotoesStatus())


async def setup(bot: commands.Bot):
    await bot.add_cog(Geral(bot))
