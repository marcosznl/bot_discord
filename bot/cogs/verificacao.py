import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.moderacao import enviar_log
from bot.emojis import emoji
from bot.permissions import cargo_perigoso, motivo_cargo_invalido, requer_permissao


class BotaoVerificar(discord.ui.DynamicItem[discord.ui.Button], template=r"verificar:(?P<cargo>\d+):(?P<dias>\d+)"):
    """Botao "Sou humano": da o cargo de membro. Cargo e idade minima da
    conta ficam no custom_id, entao o painel sobrevive a reinicios."""

    def __init__(self, cargo_id: int, idade_minima_dias: int):
        super().__init__(
            discord.ui.Button(
                label="Verificar",
                emoji=emoji("verificado"),
                style=discord.ButtonStyle.success,
                custom_id=f"verificar:{cargo_id}:{idade_minima_dias}",
            )
        )
        self.cargo_id = cargo_id
        self.idade_minima_dias = idade_minima_dias

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["cargo"]), int(match["dias"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        membro = interaction.user
        cargo = interaction.guild.get_role(self.cargo_id) if interaction.guild else None
        if cargo is None or not isinstance(membro, discord.Member):
            await interaction.response.send_message("O cargo de verificacao nao existe mais. Avise a moderacao.", ephemeral=True)
            return
        if cargo in membro.roles:
            await interaction.response.send_message("Voce ja esta verificado(a).", ephemeral=True)
            return
        if cargo.managed or cargo >= interaction.guild.me.top_role or cargo_perigoso(cargo):
            await interaction.response.send_message("Nao consigo dar esse cargo. Avise a moderacao.", ephemeral=True)
            return

        idade = (discord.utils.utcnow() - membro.created_at).days
        if idade < self.idade_minima_dias:
            await interaction.response.send_message(
                f"Sua conta precisa ter pelo menos {self.idade_minima_dias} dia(s) para entrar. "
                "Fale com a moderacao se precisar de ajuda.",
                ephemeral=True,
            )
            await enviar_log(
                interaction.guild,
                "Verificacao recusada",
                f"{membro.mention} ({membro}) tentou se verificar com uma conta de {idade} dia(s) "
                f"(minimo: {self.idade_minima_dias}).",
                discord.Color.orange(),
            )
            return

        await membro.add_roles(cargo, reason="Verificacao")
        await interaction.response.send_message("Pronto, voce foi verificado(a)! Bem-vindo(a).", ephemeral=True)


class Verificacao(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verificacao", description="Publica um painel de verificacao que da o cargo de membro")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        cargo="Cargo dado a quem se verificar",
        idade_minima_dias="Idade minima da conta do Discord, contra contas falsas (0 = sem minimo)",
        mensagem="Texto do painel (opcional)",
    )
    async def verificacao(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
        idade_minima_dias: app_commands.Range[int, 0, 365] = 0,
        mensagem: app_commands.Range[str, 1, 1000] | None = None,
    ):
        if not await requer_permissao(interaction, "manage_roles", "esse comando"):
            return
        motivo = motivo_cargo_invalido(cargo, interaction.user)
        if motivo:
            await interaction.response.send_message(f"Nao da para usar {cargo.mention}: {motivo}.", ephemeral=True)
            return

        texto = mensagem or "Clique no botao para confirmar que voce e uma pessoa e liberar o servidor."
        if idade_minima_dias:
            texto += f"\n-# Contas com menos de {idade_minima_dias} dia(s) nao sao aceitas."
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.Section(f"## Verificacao\n{texto}", accessory=BotaoVerificar(cargo.id, idade_minima_dias)),
                accent_color=discord.Color.green(),
            )
        )
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(view=view)
        await interaction.followup.send(
            "Painel publicado. Dica: deixe os outros canais visiveis so para quem tem o cargo "
            f"{cargo.mention}, e este canal visivel para todo mundo.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoVerificar)
    await bot.add_cog(Verificacao(bot))
