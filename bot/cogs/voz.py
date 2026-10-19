import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import banco, configstore
from bot.permissions import requer_permissao

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
    som = app_commands.Group(name="som", description="Soundboard do servidor", guild_only=True)
    palco = app_commands.Group(
        name="palco",
        description="Abre e organiza palcos (canais de palco)",
        guild_only=True,
        default_permissions=discord.Permissions(manage_channels=True),
    )

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

    @som.command(name="tocar", description="Toca um som do soundboard no seu canal de voz")
    @app_commands.describe(som="Comece a digitar o nome do som")
    async def som_tocar(self, interaction: discord.Interaction, som: str):
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

    @som_tocar.autocomplete("som")
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


    @som.command(name="adicionar", description="Adiciona um som ao soundboard do servidor")
    @app_commands.describe(
        nome="Nome do som",
        audio="MP3 ou OGG de ate 512 KB e 5,2 segundos",
        emoji="Emoji do som (opcional)",
        volume="Volume de 1 a 100 (padrao 100)",
    )
    async def som_adicionar(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 2, 32],
        audio: discord.Attachment,
        emoji: str | None = None,
        volume: app_commands.Range[int, 1, 100] = 100,
    ):
        if not await requer_permissao(interaction, "create_expressions", "esse comando"):
            return
        if audio.content_type not in ("audio/mpeg", "audio/ogg") or audio.size > 512 * 1024:
            await interaction.response.send_message("Envie um MP3 ou OGG de ate 512 KB.", ephemeral=True)
            return
        await interaction.response.defer()
        criado = await interaction.guild.create_soundboard_sound(
            name=nome,
            sound=await audio.read(),
            volume=volume / 100,
            emoji=emoji.strip() if emoji else None,
            reason=f"Adicionado por {interaction.user}",
        )
        await interaction.followup.send(f"🔊 Som **{criado.name}** adicionado. Toque com `/som tocar`.")

    # ---- palcos

    @palco.command(name="abrir", description="Abre um palco com um tema")
    @app_commands.describe(canal="Canal de palco", tema="Sobre o que e o palco", notificar="Avisar todo mundo que o palco comecou?")
    async def palco_abrir(
        self,
        interaction: discord.Interaction,
        canal: discord.StageChannel,
        tema: app_commands.Range[str, 1, 120],
        notificar: bool = False,
    ):
        if not await requer_permissao(interaction, "manage_channels", "esse comando"):
            return
        if canal.instance is not None:
            await canal.instance.edit(topic=tema)
            await interaction.response.send_message(f"Tema do palco {canal.mention} trocado para **{tema}**.")
            return
        await interaction.response.defer()
        await canal.create_instance(
            topic=tema, send_start_notification=notificar, reason=f"Aberto por {interaction.user}"
        )
        await interaction.followup.send(f"🎙️ Palco aberto em {canal.mention}: **{tema}**")

    @palco.command(name="fechar", description="Encerra o palco")
    @app_commands.describe(canal="Canal de palco")
    async def palco_fechar(self, interaction: discord.Interaction, canal: discord.StageChannel):
        if not await requer_permissao(interaction, "manage_channels", "esse comando"):
            return
        if canal.instance is None:
            await interaction.response.send_message("Esse palco nao esta aberto.", ephemeral=True)
            return
        await canal.instance.delete(reason=f"Fechado por {interaction.user}")
        await interaction.response.send_message(f"Palco {canal.mention} encerrado.")

    @palco.command(name="palestrante", description="Sobe alguem da plateia para falar no palco")
    @app_commands.describe(membro="Quem vai falar (precisa estar no palco)")
    async def palco_palestrante(self, interaction: discord.Interaction, membro: discord.Member):
        await self._mover_no_palco(interaction, membro, falar=True)

    @palco.command(name="plateia", description="Desce alguem para a plateia")
    @app_commands.describe(membro="Quem vai para a plateia")
    async def palco_plateia(self, interaction: discord.Interaction, membro: discord.Member):
        await self._mover_no_palco(interaction, membro, falar=False)

    async def _mover_no_palco(self, interaction: discord.Interaction, membro: discord.Member, falar: bool) -> None:
        if not await requer_permissao(interaction, "manage_channels", "esse comando"):
            return
        if membro.voice is None or not isinstance(membro.voice.channel, discord.StageChannel):
            await interaction.response.send_message(f"{membro.mention} nao esta em um palco.", ephemeral=True)
            return
        await membro.edit(suppress=not falar)
        texto = f"{membro.mention} agora pode falar." if falar else f"{membro.mention} voltou para a plateia."
        await interaction.response.send_message(texto)


async def setup(bot: commands.Bot):
    await bot.add_cog(Voz(bot))
