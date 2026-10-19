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
    "create_expressions": "Criar Expressoes",
}


# Permissoes que um cargo entregue pelo bot por clique (painel de cargos,
# verificacao, candidatura aprovada) nunca deveria dar - senao qualquer
# membro viraria moderador com um botao.
PERMISSOES_PERIGOSAS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    manage_messages=True,
    manage_threads=True,
    manage_webhooks=True,
    manage_expressions=True,
    manage_events=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    mention_everyone=True,
    view_audit_log=True,
)


def cargo_perigoso(cargo: discord.Role) -> bool:
    return bool(cargo.permissions.value & PERMISSOES_PERIGOSAS.value)


def motivo_cargo_invalido(cargo: discord.Role, autor: discord.Member) -> str | None:
    """Por que o bot nao deveria entregar esse cargo automaticamente (ou None
    se pode)."""
    guild = cargo.guild
    if cargo.is_default() or cargo.managed:
        return "e um cargo automatico (de bot, impulso ou @everyone)"
    if cargo >= guild.me.top_role:
        return "esta acima (ou no mesmo nivel) do meu cargo"
    if autor.id != guild.owner_id and cargo >= autor.top_role:
        return "esta acima (ou no mesmo nivel) do seu cargo"
    if cargo_perigoso(cargo):
        return "da permissoes de moderacao/administracao"
    return None


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
