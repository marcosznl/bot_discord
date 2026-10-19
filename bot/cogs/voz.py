import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import banco, configstore

logger = logging.getLogger(__name__)

CHAVE_CRIADOR = "salas_criador"

banco.registrar_schema(
    "CREATE TABLE IF NOT EXISTS salas_temporarias ("
    "canal_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, dono_id INTEGER NOT NULL)"
)


def sala_registrar(canal_id: int, guild_id: int, dono_id: int) -> None:
    banco.executar(
        "INSERT INTO salas_temporarias (canal_id, guild_id, dono_id) VALUES (?, ?, ?) "
        "ON CONFLICT (canal_id) DO UPDATE SET dono_id = excluded.dono_id",
        (canal_id, guild_id, dono_id),
    )


def sala_dono(canal_id: int) -> int | None:
    linha = banco.um("SELECT dono_id FROM salas_temporarias WHERE canal_id = ?", (canal_id,))
    return linha[0] if linha else None


def sala_remover(canal_id: int) -> None:
    banco.executar("DELETE FROM salas_temporarias WHERE canal_id = ?", (canal_id,))


def salas_todas() -> list[tuple[int, int]]:
    return banco.todos("SELECT canal_id, guild_id FROM salas_temporarias")


class Voz(commands.Cog):
    sala = app_commands.Group(name="sala", description="Controla a sua sala de voz temporaria", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sons_padrao: list[discord.SoundboardDefaultSound] | None = None
        self._limpeza_feita = False

    # ---- salas temporarias

    @commands.Cog.listener()
    async def on_ready(self):
        # Salas que ficaram vazias enquanto o bot estava desligado.
        if self._limpeza_feita:
            return
        self._limpeza_feita = True
        for canal_id, _ in await asyncio.to_thread(salas_todas):
            canal = self.bot.get_channel(canal_id)
            if canal is None:
                await asyncio.to_thread(sala_remover, canal_id)
            elif not canal.members:
                await self._apagar_sala(canal)

    async def _apagar_sala(self, canal: discord.VoiceChannel) -> None:
        await asyncio.to_thread(sala_remover, canal.id)
        try:
            await canal.delete(reason="Sala temporaria vazia")
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui apagar a sala temporaria {canal.id}: {erro}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, membro: discord.Member, antes: discord.VoiceState, depois: discord.VoiceState):
        if antes.channel == depois.channel:
            return

        if antes.channel is not None and not antes.channel.members:
            if await asyncio.to_thread(sala_dono, antes.channel.id) is not None:
                await self._apagar_sala(antes.channel)

        if depois.channel is None or membro.bot:
            return
        criador_id = await asyncio.to_thread(configstore.obter, membro.guild.id, CHAVE_CRIADOR)
        if depois.channel.id != criador_id:
            return

        criador = depois.channel
        try:
            sala = await membro.guild.create_voice_channel(
                name=f"Sala de {membro.display_name}"[:100],
                category=criador.category,
                bitrate=criador.bitrate,
                overwrites={
                    **criador.overwrites,
                    membro: discord.PermissionOverwrite(
                        connect=True, manage_channels=True, move_members=True, mute_members=True
                    ),
                },
                reason=f"Sala temporaria de {membro}",
            )
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui criar sala temporaria em {membro.guild.id}: {erro}")
            return
        await asyncio.to_thread(sala_registrar, sala.id, membro.guild.id, membro.id)
        try:
            await membro.move_to(sala, reason="Sala temporaria")
        except discord.HTTPException:
            # A pessoa saiu antes de ser movida - a sala ficaria orfa.
            await self._apagar_sala(sala)

    async def _sala_do_autor(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        voz = interaction.user.voice
        canal = voz.channel if voz else None
        dono = await asyncio.to_thread(sala_dono, canal.id) if canal else None
        if dono is None:
            await interaction.response.send_message(
                "Entre na sua sala temporaria primeiro (as salas sao criadas pelo canal configurado em "
                "`/configurar salas`).",
                ephemeral=True,
            )
            return None
        if dono != interaction.user.id:
            await interaction.response.send_message(
                f"Essa sala e de <@{dono}>. So o dono pode mexer nela.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return None
        return canal

    @sala.command(name="renomear", description="Muda o nome da sua sala")
    @app_commands.describe(nome="Novo nome")
    async def renomear(self, interaction: discord.Interaction, nome: app_commands.Range[str, 1, 100]):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        # O Discord so deixa renomear canal 2x a cada 10 min - o discord.py
        # espera sozinho, por isso o defer.
        await interaction.response.defer(ephemeral=True)
        await canal.edit(name=nome)
        await interaction.followup.send(f"Sala renomeada para **{nome}**.", ephemeral=True)

    @sala.command(name="limite", description="Limita quantas pessoas cabem na sua sala")
    @app_commands.describe(pessoas="Maximo de pessoas (0 = sem limite)")
    async def limite(self, interaction: discord.Interaction, pessoas: app_commands.Range[int, 0, 99]):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        await canal.edit(user_limit=pessoas)
        texto = f"Limite: {pessoas} pessoa(s)." if pessoas else "Sala sem limite de pessoas."
        await interaction.response.send_message(texto, ephemeral=True)

    @sala.command(name="trancar", description="Ninguem novo entra na sua sala (quem ja esta fica)")
    async def trancar(self, interaction: discord.Interaction):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        await canal.set_permissions(interaction.guild.default_role, connect=False)
        for membro in canal.members:
            await canal.set_permissions(membro, connect=True)
        await interaction.response.send_message("🔒 Sala trancada.", ephemeral=True)

    @sala.command(name="destrancar", description="Libera a entrada na sua sala")
    async def destrancar(self, interaction: discord.Interaction):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        await canal.set_permissions(interaction.guild.default_role, connect=None)
        await interaction.response.send_message("🔓 Sala destrancada.", ephemeral=True)

    @sala.command(name="expulsar", description="Tira alguem da sua sala")
    @app_commands.describe(membro="Quem tirar")
    async def expulsar(self, interaction: discord.Interaction, membro: discord.Member):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        if membro.id == interaction.user.id or membro not in canal.members:
            await interaction.response.send_message("Essa pessoa nao esta na sua sala.", ephemeral=True)
            return
        await membro.move_to(None, reason=f"Expulso da sala por {interaction.user}")
        await canal.set_permissions(membro, connect=False)
        await interaction.response.send_message(f"{membro.mention} saiu da sala e nao pode voltar.", ephemeral=True)

    @sala.command(name="transferir", description="Passa a sua sala para outra pessoa")
    @app_commands.describe(membro="Novo dono (precisa estar na sala)")
    async def transferir(self, interaction: discord.Interaction, membro: discord.Member):
        canal = await self._sala_do_autor(interaction)
        if canal is None:
            return
        if membro.bot or membro not in canal.members:
            await interaction.response.send_message("O novo dono precisa estar na sala.", ephemeral=True)
            return
        await asyncio.to_thread(sala_registrar, canal.id, interaction.guild.id, membro.id)
        await canal.set_permissions(interaction.user, overwrite=None)
        await canal.set_permissions(
            membro, connect=True, manage_channels=True, move_members=True, mute_members=True
        )
        await interaction.response.send_message(f"A sala agora e de {membro.mention}.")

    # ---- soundboard

    async def _sons_do_discord(self) -> list[discord.SoundboardDefaultSound]:
        if self._sons_padrao is None:
            self._sons_padrao = await self.bot.fetch_soundboard_default_sounds()
        return self._sons_padrao

    @app_commands.command(name="som", description="Toca um som do soundboard no seu canal de voz")
    @app_commands.guild_only()
    @app_commands.describe(som="Comece a digitar o nome do som")
    async def som(self, interaction: discord.Interaction, som: str):
        voz = interaction.user.voice
        if voz is None or voz.channel is None:
            await interaction.response.send_message("Voce precisa estar em um canal de voz.", ephemeral=True)
            return

        tipo, _, sound_id = som.partition(":")
        if not sound_id.isdigit():
            await interaction.response.send_message("Escolha um som da lista.", ephemeral=True)
            return
        if tipo == "s":
            escolhido = interaction.guild.get_soundboard_sound(int(sound_id))
        else:
            escolhido = discord.utils.get(await self._sons_do_discord(), id=int(sound_id))
        if escolhido is None:
            await interaction.response.send_message("Nao achei esse som.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        # O Discord so deixa tocar som num canal em que o bot esta conectado.
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await voz.channel.connect()
        elif voice_client.channel != voz.channel:
            if voice_client.is_playing():
                await interaction.followup.send(
                    "Estou tocando musica em outro canal agora.", ephemeral=True
                )
                return
            await voice_client.move_to(voz.channel)
        await voz.channel.send_sound(escolhido)
        await interaction.followup.send(f"🔊 {escolhido.name}", ephemeral=True)

    @som.autocomplete("som")
    async def _som_autocomplete(self, interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[str]]:
        atual = atual.lower()
        opcoes = [
            app_commands.Choice(name=f"{s.emoji or '🔊'} {s.name} (servidor)"[:100], value=f"s:{s.id}")
            for s in (interaction.guild.soundboard_sounds if interaction.guild else [])
            if s.available and atual in s.name.lower()
        ]
        try:
            padrao = await self._sons_do_discord()
        except discord.HTTPException:
            padrao = []
        opcoes += [
            app_commands.Choice(name=f"{s.emoji or '🔊'} {s.name}"[:100], value=f"d:{s.id}")
            for s in padrao
            if atual in s.name.lower()
        ]
        return opcoes[:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(Voz(bot))
