import discord

# Nome legivel de cada permissao checada pelo bot - usado nas mensagens de
# erro, que aparecem para o usuario.
NOMES_PERMISSOES = {
    "manage_guild": "Gerenciar Servidor",
    "manage_messages": "Gerenciar Mensagens",
    "manage_roles": "Gerenciar Cargos",
    "manage_channels": "Gerenciar Canais",
    "manage_events": "Gerenciar Eventos",
    "moderate_members": "Moderar Membros",
    "kick_members": "Expulsar Membros",
    "ban_members": "Banir Membros",
}


async def requer_permissao(interaction: discord.Interaction, permissao: str, alvo: str = "essa opcao") -> bool:
    """Confere se quem chamou esta num servidor e tem a permissao pedida; se
    nao, avisa e devolve False (o chamador deve so retornar nesse caso)."""
    if (
        interaction.guild is None
        or not isinstance(interaction.user, discord.Member)
        or not getattr(interaction.user.guild_permissions, permissao)
    ):
        nome = NOMES_PERMISSOES.get(permissao, permissao)
        await interaction.response.send_message(
            f"Voce precisa estar em um servidor e ter a permissao {nome} para usar {alvo}.",
            ephemeral=True,
        )
        return False
    return True


async def requer_manage_guild(interaction: discord.Interaction, alvo: str = "essa opcao") -> bool:
    return await requer_permissao(interaction, "manage_guild", alvo)


async def requer_hierarquia(interaction: discord.Interaction, membro: discord.Member, acao: str) -> bool:
    """Impede moderar alguem com cargo igual/maior que o seu (ou que o do
    bot) - o Discord recusaria do mesmo jeito, mas com um 403 sem explicacao."""
    guild = interaction.guild
    autor = interaction.user
    if membro.id == autor.id:
        mensagem = f"Voce nao pode {acao} voce mesmo."
    elif membro.id == guild.owner_id:
        mensagem = f"Ninguem pode {acao} o dono do servidor."
    elif autor.id != guild.owner_id and membro.top_role >= autor.top_role:
        mensagem = f"Voce nao pode {acao} alguem com cargo igual ou maior que o seu."
    elif membro.top_role >= guild.me.top_role:
        mensagem = f"Nao consigo {acao} {membro.mention}: o cargo dele(a) e igual ou maior que o meu."
    else:
        return True
    await interaction.response.send_message(mensagem, ephemeral=True)
    return False
