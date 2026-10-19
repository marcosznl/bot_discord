import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import BASE_DIR
from bot.jsonstore import escrever_json, ler_json
from bot.permissions import requer_manage_guild
from bot.retry_ui import ModalDoBot, ViewDoBot

# Configuracao de boas-vindas por servidor (canal + mensagem), definida pelo
# comando /boasvindas e guardada em BOAS_VINDAS_CONFIG_PATH - nao precisa
# mexer no .env nem reiniciar o bot para configurar.
BOAS_VINDAS_CONFIG_PATH = os.path.join(BASE_DIR, "boas_vindas.json")
BOAS_VINDAS_MENSAGEM_PADRAO = "Bem-vindo(a) ao servidor, {membro}!"


def _boas_vindas_config_bruta() -> dict:
    return ler_json(BOAS_VINDAS_CONFIG_PATH)


def _boas_vindas_config(guild_id: int) -> dict | None:
    return _boas_vindas_config_bruta().get(str(guild_id))


def _boas_vindas_definir(guild_id: int, canal_id: int | None = None, mensagem: str | None = None) -> None:
    dados = _boas_vindas_config_bruta()
    entrada = dados.setdefault(str(guild_id), {})
    if canal_id is not None:
        entrada["canal_id"] = canal_id
    if mensagem is not None:
        entrada["mensagem"] = mensagem
    escrever_json(BOAS_VINDAS_CONFIG_PATH, dados)


def _boas_vindas_desativar(guild_id: int) -> None:
    dados = _boas_vindas_config_bruta()
    dados.pop(str(guild_id), None)
    escrever_json(BOAS_VINDAS_CONFIG_PATH, dados)


class ModalBoasVindasMensagem(ModalDoBot):
    def __init__(self, guild_id: int, atual: str):
        super().__init__(title="Mensagem de boas-vindas")
        self.guild_id = guild_id
        self.mensagem = discord.ui.TextInput(
            label="Texto (use {membro} e {servidor})",
            style=discord.TextStyle.paragraph,
            default=atual,
            max_length=500,
        )
        self.add_item(self.mensagem)

    async def on_submit(self, interaction: discord.Interaction):
        await asyncio.to_thread(_boas_vindas_definir, self.guild_id, None, str(self.mensagem))
        await interaction.response.send_message("Mensagem de boas-vindas atualizada.", ephemeral=True)


class BotoesBoasVindas(ViewDoBot):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.select_canal = discord.ui.ChannelSelect(
            placeholder="Escolha o canal de boas-vindas",
            channel_types=[discord.ChannelType.text],
        )
        self.select_canal.callback = self._canal_escolhido
        self.add_item(self.select_canal)

    async def _canal_escolhido(self, interaction: discord.Interaction):
        canal = self.select_canal.values[0]
        await asyncio.to_thread(_boas_vindas_definir, self.guild_id, canal.id, None)
        await interaction.response.send_message(f"Canal de boas-vindas definido para {canal.mention}.", ephemeral=True)

    @discord.ui.button(label="Editar mensagem", style=discord.ButtonStyle.primary)
    async def editar_mensagem(self, interaction: discord.Interaction, botao: discord.ui.Button):
        atual = await asyncio.to_thread(_boas_vindas_config, self.guild_id)
        mensagem_atual = (atual or {}).get("mensagem", BOAS_VINDAS_MENSAGEM_PADRAO)
        await interaction.response.send_modal(ModalBoasVindasMensagem(self.guild_id, mensagem_atual))

    @discord.ui.button(label="Ver configuracao", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, botao: discord.ui.Button):
        config = await asyncio.to_thread(_boas_vindas_config, self.guild_id)
        if not config or not config.get("canal_id"):
            await interaction.response.send_message("Boas-vindas desativadas (nenhum canal configurado).", ephemeral=True)
            return

        canal = interaction.client.get_channel(config["canal_id"])
        canal_texto = canal.mention if canal is not None else f"<#{config['canal_id']}>"
        mensagem = config.get("mensagem", BOAS_VINDAS_MENSAGEM_PADRAO)
        await interaction.response.send_message(f"Canal: {canal_texto}\nMensagem: {mensagem}", ephemeral=True)

    @discord.ui.button(label="Desativar", style=discord.ButtonStyle.danger)
    async def desativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await asyncio.to_thread(_boas_vindas_desativar, self.guild_id)
        await interaction.response.send_message("Boas-vindas desativadas.", ephemeral=True)


class BoasVindas(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await asyncio.to_thread(_boas_vindas_config, member.guild.id)
        if not config or not config.get("canal_id"):
            return

        canal = self.bot.get_channel(config["canal_id"])
        if canal is None:
            return

        mensagem = config.get("mensagem", BOAS_VINDAS_MENSAGEM_PADRAO)
        texto = mensagem.format(membro=member.mention, servidor=member.guild.name)
        await canal.send(texto)

    @app_commands.command(name="boasvindas", description="Configura a mensagem de boas-vindas para novos membros")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def boasvindas(self, interaction: discord.Interaction):
        if not await requer_manage_guild(interaction, "esse comando"):
            return

        await interaction.response.send_message(
            "Escolha o canal e, se quiser, edite o texto de boas-vindas:",
            view=BotoesBoasVindas(interaction.guild.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BoasVindas(bot))
