import asyncio
import os
import random

import aiohttp
import discord
import psutil
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY")

intents = discord.Intents.default()

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

owner_id = None


@client.event
async def on_ready():
    global owner_id
    app_info = await client.application_info()
    owner_id = app_info.owner.id
    await tree.sync()
    print(f"Bot conectado como {client.user}")


@tree.command(name="ping", description="Testa se o bot esta online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


@tree.command(name="status", description="Mostra uso de CPU, RAM e disco da maquina")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()

    cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(os.getenv("SystemDrive", "C:") + "\\")

    embed = discord.Embed(title="Monitoramento do servidor", color=discord.Color.blue())
    embed.add_field(name="CPU", value=f"{cpu}%", inline=False)
    embed.add_field(
        name="RAM",
        value=f"{ram.percent}% ({ram.used // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        inline=False,
    )
    embed.add_field(
        name="Disco",
        value=f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
        inline=False,
    )

    await interaction.followup.send(embed=embed)


@tree.command(name="gif", description="Busca um gif pela palavra-chave")
@app_commands.describe(busca="Palavra-chave para buscar o gif")
async def gif(interaction: discord.Interaction, busca: str):
    await interaction.response.defer()

    params = {"api_key": GIPHY_API_KEY, "q": busca, "limit": 25, "rating": "pg-13"}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.giphy.com/v1/gifs/search", params=params) as resp:
            data = await resp.json()

    resultados = data.get("data")
    if not resultados:
        await interaction.followup.send(f'Nenhum gif encontrado para "{busca}".')
        return

    gif_url = random.choice(resultados)["images"]["original"]["url"]
    await interaction.followup.send(gif_url)


@tree.command(name="desligar", description="Desliga o bot (apenas o dono)")
async def desligar(interaction: discord.Interaction):
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

    await interaction.response.send_message("Desligando o bot...")
    await client.close()


if __name__ == "__main__":
    client.run(TOKEN)
