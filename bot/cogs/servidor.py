import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import configstore
from bot.permissions import requer_manage_guild, requer_permissao
from bot.retry_ui import ViewDoBot

logger = logging.getLogger(__name__)

# Lista de ids de canais onde cada mensagem nova ganha uma thread.
CHAVE_AUTO_THREAD = "auto_thread"


def nome_da_thread(texto: str, autor: str) -> str:
    primeira_linha = texto.strip().splitlines()[0] if texto.strip() else ""
    return (primeira_linha or f"Conversa de {autor}")[:90]


class EscolherCargosPergunta(ViewDoBot):
    """Depois do /configurar integracao_pergunta: escolhe os cargos que viram
    as opcoes da pergunta na integracao inicial do Discord."""

    def __init__(self, titulo: str, varias: bool):
        super().__init__(timeout=600)
        self.titulo = titulo
        self.varias = varias
        self.select = discord.ui.RoleSelect(placeholder="Cargos que viram opcoes", min_values=2, max_values=25)
        self.select.callback = self._escolhidos
        self.add_item(self.select)

    async def _escolhidos(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        opcoes = [
            discord.OnboardingPromptOption(title=cargo.name[:50], roles=[cargo])
            for cargo in sorted(self.select.values, key=lambda c: c.position, reverse=True)
        ]
        nova = discord.OnboardingPrompt(
            type=discord.OnboardingPromptType.multiple_choice,
            title=self.titulo,
            options=opcoes,
            single_select=not self.varias,
            required=False,
        )
        try:
            atual = await guild.onboarding()
            await guild.edit_onboarding(prompts=[*atual.prompts, nova], reason=f"Pergunta criada por {interaction.user}")
        except discord.HTTPException as erro:
            await interaction.followup.send(
                "O Discord recusou a pergunta. A integracao inicial so existe em servidores da Comunidade "
                f"e eu preciso de Gerenciar Servidor e Gerenciar Cargos.\n-# {erro.text or erro}",
                ephemeral=True,
            )
            return
        self.stop()
        await interaction.followup.send(
            f"Pergunta **{self.titulo}** adicionada a integracao inicial com {len(opcoes)} opcoes.", ephemeral=True
        )


class Servidor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._auto_thread: dict[int, list[int]] = {}

    def esquecer_auto_thread(self, guild_id: int) -> None:
        self._auto_thread.pop(guild_id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return
        guild_id = message.guild.id
        if guild_id not in self._auto_thread:
            self._auto_thread[guild_id] = await asyncio.to_thread(configstore.obter, guild_id, CHAVE_AUTO_THREAD, [])
        if message.channel.id not in self._auto_thread[guild_id]:
            return
        try:
            await message.create_thread(
                name=nome_da_thread(message.content, message.author.display_name), auto_archive_duration=1440
            )
        except discord.HTTPException as erro:
            logger.warning(f"Nao consegui criar a thread automatica em {message.channel.id}: {erro}")

    @app_commands.command(name="cargo_visual", description="Muda cor (com degrade), icone ou emoji de um cargo")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        cargo="Cargo a mudar",
        cor="Cor principal em hexadecimal, ex: #ff8800",
        cor_degrade="Segunda cor, para degrade (servidores com o recurso de cores avancadas)",
        icone="Imagem do icone do cargo (servidores nivel 2 de impulso)",
        emoji="Ou um emoji comum como icone (ex: 🔥)",
    )
    async def cargo_visual(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
        cor: str | None = None,
        cor_degrade: str | None = None,
        icone: discord.Attachment | None = None,
        emoji: str | None = None,
    ):
        if not await requer_permissao(interaction, "manage_roles", "esse comando"):
            return
        guild = interaction.guild
        if cargo >= guild.me.top_role or (interaction.user.id != guild.owner_id and cargo >= interaction.user.top_role):
            await interaction.response.send_message("Esse cargo esta acima do seu ou do meu.", ephemeral=True)
            return

        mudancas: dict = {}
        try:
            if cor:
                mudancas["color"] = discord.Color.from_str(cor if cor.startswith("#") else f"#{cor}")
            if cor_degrade:
                if "ENHANCED_ROLE_COLORS" not in guild.features:
                    raise LookupError("Este servidor ainda nao tem cores em degrade para cargos.")
                mudancas["secondary_color"] = discord.Color.from_str(
                    cor_degrade if cor_degrade.startswith("#") else f"#{cor_degrade}"
                )
            if icone is not None or emoji:
                if "ROLE_ICONS" not in guild.features:
                    raise LookupError("Icone de cargo precisa do nivel 2 de impulso.")
                mudancas["display_icon"] = await icone.read() if icone is not None else emoji.strip()
        except ValueError:
            await interaction.response.send_message("Cor invalida. Use algo como `#ff8800`.", ephemeral=True)
            return
        except LookupError as erro:
            await interaction.response.send_message(str(erro), ephemeral=True)
            return
        if not mudancas:
            await interaction.response.send_message("Diga pelo menos uma coisa para mudar.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await cargo.edit(**mudancas, reason=f"Visual alterado por {interaction.user}")
        await interaction.followup.send(f"Visual de {cargo.mention} atualizado.", ephemeral=True)

    # Comandos de configuracao que dependem deste cog ficam no /configurar
    # (bot/cogs/configuracao.py) - aqui so as acoes.

    async def tela_boas_vindas(
        self, interaction: discord.Interaction, descricao: str, canais: list[tuple[discord.abc.GuildChannel, str]]
    ) -> None:
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.edit_welcome_screen(
                description=descricao,
                welcome_channels=[discord.WelcomeChannel(channel=canal, description=texto[:50]) for canal, texto in canais],
                enabled=True,
                reason=f"Por {interaction.user}",
            )
        except discord.HTTPException as erro:
            await interaction.followup.send(
                "O Discord recusou. A tela de boas-vindas so existe em servidores da Comunidade "
                f"e eu preciso de Gerenciar Servidor.\n-# {erro.text or erro}",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Tela de boas-vindas atualizada.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Servidor(bot))
