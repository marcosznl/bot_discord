import discord

from bot.config import USUARIOS_AUTORIZADOS


async def requer_usuario_autorizado(interaction: discord.Interaction, alvo: str = "esse comando") -> bool:
    """Confere se quem chamou esta na lista de USUARIOS_AUTORIZADOS - usado
    pelos comandos pessoais (status), que so funcionam por DM, onde nao
    existe cargo/permissao de servidor pra checar."""
    if interaction.user.id not in USUARIOS_AUTORIZADOS:
        await interaction.response.send_message(f"Voce nao tem autorizacao para usar {alvo}.", ephemeral=True)
        return False
    return True


async def requer_manage_guild(interaction: discord.Interaction, alvo: str = "essa opcao") -> bool:
    """Confere se quem chamou esta num servidor e tem Gerenciar Servidor; se
    nao, avisa e devolve False (o chamador deve so retornar nesse caso)."""
    if (
        interaction.guild is None
        or not isinstance(interaction.user, discord.Member)
        or not interaction.user.guild_permissions.manage_guild
    ):
        await interaction.response.send_message(
            f"Voce precisa estar em um servidor e ter a permissao Gerenciar Servidor para usar {alvo}.",
            ephemeral=True,
        )
        return False
    return True
