import discord


async def requer_manage_guild(interaction: discord.Interaction, alvo: str = "essa opcao") -> bool:
    """Confere se quem chamou esta num servidor e tem Gerenciar Servidor; se
    nao, avisa e devolve False (o chamador deve so retornar nesse caso)."""
    if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            f"Voce precisa estar em um servidor e ter a permissao Gerenciar Servidor para usar {alvo}.",
            ephemeral=True,
        )
        return False
    return True
