import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot.config import GIPHY_API_KEY


async def _buscar_gif(busca: str) -> str | None:
    params = {"api_key": GIPHY_API_KEY, "q": busca, "limit": 25, "rating": "pg-13"}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.giphy.com/v1/gifs/search", params=params) as resp:
            data = await resp.json()

    resultados = data.get("data")
    if not resultados:
        return None
    return random.choice(resultados)["images"]["original"]["url"]


class Diversao(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gif", description="Busca um gif pela palavra-chave")
    @app_commands.describe(busca="Palavra-chave para buscar o gif")
    async def gif(self, interaction: discord.Interaction, busca: str):
        await interaction.response.defer()

        gif_url = await _buscar_gif(busca)
        if gif_url is None:
            await interaction.followup.send(f'Nenhum gif encontrado para "{busca}".')
            return

        await interaction.followup.send(gif_url)

    @app_commands.command(name="socao", description="Da um socao (com gif de anime) em alguem")
    @app_commands.describe(pessoa="Quem vai levar o socao")
    async def socao(self, interaction: discord.Interaction, pessoa: discord.Member):
        await interaction.response.defer()

        gif_url = await _buscar_gif("anime punch")
        if gif_url is None:
            await interaction.followup.send("Nao encontrei nenhum gif de socao agora.")
            return

        embed = discord.Embed()
        embed.set_image(url=gif_url)
        await interaction.followup.send(
            content=f"{interaction.user.mention} deu um socao em {pessoa.mention}", embed=embed
        )

    @app_commands.command(name="abraco", description="Da um abraco (com gif de anime) em alguem")
    @app_commands.describe(pessoa="Quem vai levar o abraco")
    async def abraco(self, interaction: discord.Interaction, pessoa: discord.Member):
        await interaction.response.defer()

        gif_url = await _buscar_gif("anime hug")
        if gif_url is None:
            await interaction.followup.send("Nao encontrei nenhum gif de abraco agora.")
            return

        embed = discord.Embed()
        embed.set_image(url=gif_url)
        await interaction.followup.send(
            content=f"{interaction.user.mention} deu um abraco em {pessoa.mention}", embed=embed
        )

    @app_commands.command(name="charme", description="Joga um charme (com gif de anime) para alguem")
    @app_commands.describe(pessoa="Para quem jogar o charme")
    async def charme(self, interaction: discord.Interaction, pessoa: discord.Member):
        await interaction.response.defer()

        gif_url = await _buscar_gif("anime flirt")
        if gif_url is None:
            await interaction.followup.send("Nao encontrei nenhum gif de charme agora.")
            return

        embed = discord.Embed()
        embed.set_image(url=gif_url)
        await interaction.followup.send(
            content=f"{interaction.user.mention} jogou um charme para {pessoa.mention}", embed=embed
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Diversao(bot))
