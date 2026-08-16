import asyncio
import json
import subprocess

import discord
from discord import app_commands
from discord.ext import commands


def _teste_velocidade() -> dict:
    resultado = subprocess.run(
        ["speedtest", "--accept-license", "--accept-gdpr", "--format=json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(resultado.stdout)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="velocidade_server", description="Testa a velocidade da internet da maquina")
    async def velocidade_server(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            dados = await asyncio.to_thread(_teste_velocidade)
        except Exception as erro:
            print(f"Erro no teste de velocidade: {erro}")
            await interaction.followup.send("Nao consegui rodar o teste de velocidade agora.")
            return

        download_mbps = dados["download"]["bandwidth"] * 8 / 1_000_000
        upload_mbps = dados["upload"]["bandwidth"] * 8 / 1_000_000
        ping_ms = dados["ping"]["latency"]

        embed = discord.Embed(title="Teste de velocidade", color=discord.Color.blue())
        embed.add_field(name="Medida", value="Download\nUpload\nPing", inline=True)
        embed.add_field(
            name="Valor",
            value=f"{download_mbps:.1f} Mbps\n{upload_mbps:.1f} Mbps\n{ping_ms:.0f} ms",
            inline=True,
        )
        embed.set_footer(text=f"Servidor: {dados['server']['name']} ({dados['server']['location']})")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="desligar", description="Desliga o bot (apenas o dono)")
    async def desligar(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
            return

        await interaction.response.send_message("Desligando o bot...")
        await self.bot.close()

    @app_commands.command(name="reiniciar", description="Reinicia o bot (apenas o dono)")
    async def reiniciar(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
            return

        await interaction.response.send_message("Reiniciando o bot...")
        await self.bot.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
