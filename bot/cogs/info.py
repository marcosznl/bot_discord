import discord
from discord import app_commands
from discord.ext import commands

from bot import layout
from bot.cogs.xp import xp_cartao_rank

# Mencoes dentro de cartoes (cargos, dono do servidor) sao so informativas -
# sem isso o Discord notificaria cada pessoa/cargo citado.
SEM_MENCOES = discord.AllowedMentions.none()


async def cartao_usuario(client: discord.Client, usuario: discord.abc.User) -> discord.ui.LayoutView:
    # fetch_user traz banner e cor de destaque, que nao vem no objeto do evento.
    completo = await client.fetch_user(usuario.id)
    membro = usuario if isinstance(usuario, discord.Member) else None

    linhas = [
        f"## {usuario.display_name}",
        f"**Usuario:** {usuario.name}" + (" (bot)" if usuario.bot else ""),
        f"**ID:** `{usuario.id}`",
        f"**Conta criada:** {discord.utils.format_dt(usuario.created_at, 'D')} ({discord.utils.format_dt(usuario.created_at, 'R')})",
    ]
    if membro is not None:
        if membro.joined_at:
            linhas.append(
                f"**Entrou no servidor:** {discord.utils.format_dt(membro.joined_at, 'D')} "
                f"({discord.utils.format_dt(membro.joined_at, 'R')})"
            )
        if membro.premium_since:
            linhas.append(f"**Impulsiona desde:** {discord.utils.format_dt(membro.premium_since, 'D')}")
        if membro.is_timed_out():
            linhas.append(f"**Silenciado ate:** {discord.utils.format_dt(membro.timed_out_until, 'f')}")

    itens: list[discord.ui.Item] = [layout.secao_com_miniatura("\n".join(linhas), usuario.display_avatar.url)]

    if membro is not None:
        cargos = [cargo.mention for cargo in reversed(membro.roles) if not cargo.is_default()]
        if cargos:
            texto_cargos = " ".join(cargos[:30]) + (f" e mais {len(cargos) - 30}" if len(cargos) > 30 else "")
            itens += [layout.separador(), layout.texto(f"**Cargos ({len(cargos)}):** {texto_cargos}")]

    if completo.banner is not None:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(completo.banner.url)))

    cor = completo.accent_color or (membro.color if membro and membro.color.value else discord.Color.blurple())
    return layout.view_estatica(*itens, cor=cor)


def cartao_avatar(usuario: discord.abc.User) -> discord.ui.LayoutView:
    galeria = discord.ui.MediaGallery(discord.MediaGalleryItem(usuario.display_avatar.with_size(1024).url))
    # Avatar especifico do servidor (quando a pessoa tem um) + o global.
    if isinstance(usuario, discord.Member) and usuario.guild_avatar is not None:
        galeria.add_item(media=usuario.avatar.with_size(1024).url if usuario.avatar else usuario.default_avatar.url)
    links = " | ".join(
        f"[{formato.upper()}]({usuario.display_avatar.with_format(formato).with_size(1024).url})"
        for formato in ("png", "jpg", "webp")
    )
    return layout.view_estatica(
        layout.texto(f"## Avatar de {usuario.display_name}"),
        galeria,
        layout.texto(f"-# Baixar: {links}"),
        cor=discord.Color.blurple(),
    )


def cartao_servidor(guild: discord.Guild) -> discord.ui.LayoutView:
    texto_canais = len(guild.text_channels)
    voz_canais = len(guild.voice_channels) + len(guild.stage_channels)
    linhas = [
        f"## {guild.name}",
        f"**Dono:** <@{guild.owner_id}>",
        f"**Criado em:** {discord.utils.format_dt(guild.created_at, 'D')} ({discord.utils.format_dt(guild.created_at, 'R')})",
        f"**Membros:** {guild.member_count}",
        f"**Canais:** {texto_canais} de texto, {voz_canais} de voz, {len(guild.forums)} forum(ns)",
        f"**Cargos:** {len(guild.roles) - 1} | **Emojis:** {len(guild.emojis)} | **Figurinhas:** {len(guild.stickers)}",
        f"**Impulsos:** {guild.premium_subscription_count} (nivel {guild.premium_tier})",
        f"**Verificacao:** {str(guild.verification_level).replace('_', ' ')}",
    ]
    itens: list[discord.ui.Item] = [
        layout.secao_com_miniatura("\n".join(linhas), guild.icon.url if guild.icon else None)
    ]
    if guild.description:
        itens += [layout.separador(), layout.texto(guild.description)]
    if guild.banner is not None:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(guild.banner.url)))
    itens.append(layout.texto(f"-# ID: {guild.id}"))
    return layout.view_estatica(*itens, cor=discord.Color.blurple())


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Menus de clique direito (Apps) - context menus nao podem ser
        # declarados com decorator dentro de um cog, entao entram na arvore aqui.
        self.menus = [
            app_commands.ContextMenu(name="Ver perfil", callback=self.menu_perfil),
            app_commands.ContextMenu(name="Ver avatar", callback=self.menu_avatar),
            app_commands.ContextMenu(name="Ver rank XP", callback=self.menu_rank),
        ]
        for menu in self.menus:
            self.bot.tree.add_command(menu)

    async def cog_unload(self) -> None:
        for menu in self.menus:
            self.bot.tree.remove_command(menu.name, type=menu.type)

    # ---- menus de clique direito em usuario

    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def menu_perfil(self, interaction: discord.Interaction, usuario: discord.User):
        await interaction.response.defer(ephemeral=True)
        view = await cartao_usuario(interaction.client, usuario)
        await interaction.followup.send(view=view, ephemeral=True, allowed_mentions=SEM_MENCOES)

    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def menu_avatar(self, interaction: discord.Interaction, usuario: discord.User):
        await interaction.response.send_message(view=cartao_avatar(usuario), ephemeral=True)

    @app_commands.guild_only()
    async def menu_rank(self, interaction: discord.Interaction, membro: discord.Member):
        if membro.bot:
            await interaction.response.send_message("Bots nao ganham XP.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        view = await xp_cartao_rank(interaction.client, interaction.guild.id, membro)
        await interaction.followup.send(view=view, ephemeral=True, allowed_mentions=SEM_MENCOES)

    # ---- comandos

    @app_commands.command(name="usuario", description="Mostra o perfil de alguem (ou o seu)")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(usuario="De quem ver o perfil (vazio = voce)")
    async def usuario(self, interaction: discord.Interaction, usuario: discord.User | None = None):
        await interaction.response.defer()
        view = await cartao_usuario(interaction.client, usuario or interaction.user)
        await interaction.followup.send(view=view, allowed_mentions=SEM_MENCOES)

    @app_commands.command(name="avatar", description="Mostra o avatar de alguem em tamanho grande")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(usuario="De quem ver o avatar (vazio = voce)")
    async def avatar(self, interaction: discord.Interaction, usuario: discord.User | None = None):
        await interaction.response.send_message(view=cartao_avatar(usuario or interaction.user))

    @app_commands.command(name="servidor", description="Mostra informacoes deste servidor")
    @app_commands.guild_only()
    async def servidor(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            view=cartao_servidor(interaction.guild), allowed_mentions=SEM_MENCOES
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Info(bot))
