import asyncio
import json
import logging
import os
import platform
import subprocess

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from bot.permissions import requer_usuario_autorizado

logger = logging.getLogger(__name__)

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "ajuda"],
    "Moderacao": ["limpar", "boasvindas"],
    "Niveis": ["xp"],
    "Diversao": ["gif", "socao", "abraco", "charme"],
    "Musica": ["musica"],
}


def _teste_velocidade() -> dict:
    resultado = subprocess.run(
        ["speedtest", "--accept-license", "--accept-gdpr", "--format=json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(resultado.stdout)


def _todos_comandos_registrados(bot: commands.Bot) -> dict[str, app_commands.Command]:
    resultado = {}
    for item in bot.tree.get_commands():
        if isinstance(item, app_commands.Group):
            for sub in item.commands:
                resultado[f"{item.name} {sub.name}"] = sub
        elif isinstance(item, app_commands.Command):
            resultado[item.name] = item
    return resultado


async def _status_embed_maquina() -> discord.Embed:
    cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
    ram = psutil.virtual_memory()
    if platform.system() == "Windows":
        disk_path = os.getenv("SystemDrive", "C:") + "\\"
    elif platform.system() == "Darwin" and os.path.exists("/System/Volumes/Data"):
        disk_path = "/System/Volumes/Data"
    else:
        disk_path = "/"
    disk = psutil.disk_usage(disk_path)

    embed = discord.Embed(title="Monitoramento do servidor", color=discord.Color.blue())
    topicos = ["CPU", "RAM", "Disco"]
    valores = [
        f"{cpu}%",
        f"{ram.percent}% ({(ram.total - ram.available) // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
    ]

    try:
        dados_velocidade = await asyncio.to_thread(_teste_velocidade)
    except Exception as erro:
        logger.error(f"Erro no teste de velocidade: {erro}")
        topicos.append("Internet")
        valores.append("Nao consegui testar agora")
    else:
        download_mbps = dados_velocidade["download"]["bandwidth"] * 8 / 1_000_000
        upload_mbps = dados_velocidade["upload"]["bandwidth"] * 8 / 1_000_000
        ping_ms = dados_velocidade["ping"]["latency"]
        topicos += ["Download", "Upload", "Ping"]
        valores += [f"{download_mbps:.1f} Mbps", f"{upload_mbps:.1f} Mbps", f"{ping_ms:.0f} ms"]
        servidor_speedtest = dados_velocidade["server"]
        embed.set_footer(text=f"Speedtest via: {servidor_speedtest['name']} ({servidor_speedtest['location']})")

    embed.add_field(name="Recurso", value="\n".join(topicos), inline=True)
    embed.add_field(name="Valor", value="\n".join(valores), inline=True)
    return embed


class Geral(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Testa se o bot esta online")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("Pong!")

    @app_commands.command(name="limpar", description="Apaga mensagens recentes deste canal")
    @app_commands.guild_only()
    @app_commands.describe(quantidade="Quantas mensagens apagar (1 a 200)")
    @app_commands.default_permissions(manage_messages=True)
    async def limpar(self, interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 200]):
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.purge(limit=quantidade)
        await interaction.delete_original_response()

    @app_commands.command(name="ajuda", description="Mostra todos os comandos do bot, por categoria")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
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

    @app_commands.command(name="status", description="Mostra o status da maquina")
    # So DM/chat privado - nao aparece em nenhum servidor (comando pessoal).
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=False, users=True)
    async def status(self, interaction: discord.Interaction):
        if not await requer_usuario_autorizado(interaction, "esse comando"):
            return

        await interaction.response.defer()
        await interaction.followup.send(embed=await _status_embed_maquina())


async def setup(bot: commands.Bot):
    await bot.add_cog(Geral(bot))
