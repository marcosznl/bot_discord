import asyncio
import os

import discord
import psutil
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
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

    embed = discord.Embed(title="Monitoramento da maquina", color=discord.Color.blue())
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


if __name__ == "__main__":
    client.run(TOKEN)
