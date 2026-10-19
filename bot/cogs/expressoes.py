import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.permissions import requer_permissao

TAMANHO_MAX_EMOJI = 256 * 1024
TAMANHO_MAX_FIGURINHA = 512 * 1024
EMOJI_CUSTOM = re.compile(r"<(a?):(\w{2,32}):(\d+)>")


def emojis_no_texto(texto: str) -> list[discord.PartialEmoji]:
    """Emojis personalizados (<:nome:id> / <a:nome:id>) sem repetir."""
    vistos: dict[int, discord.PartialEmoji] = {}
    for animado, nome, emoji_id in EMOJI_CUSTOM.findall(texto):
        vistos.setdefault(int(emoji_id), discord.PartialEmoji(name=nome, id=int(emoji_id), animated=bool(animado)))
    return list(vistos.values())


def nome_valido(nome: str) -> str | None:
    nome = re.sub(r"\W", "_", nome.strip())[:32]
    return nome if len(nome) >= 2 else None


async def _vagas_emoji(guild: discord.Guild, animado: bool) -> int:
    usados = sum(1 for e in guild.emojis if e.animated == animado)
    return guild.emoji_limit - usados


class Expressoes(commands.Cog):
    emoji = app_commands.Group(
        name="emoji",
        description="Adiciona emojis e figurinhas ao servidor",
        guild_only=True,
        default_permissions=discord.Permissions(create_expressions=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.menu_copiar = app_commands.ContextMenu(name="Adicionar emojis ao servidor", callback=self.copiar_da_mensagem)
        self.bot.tree.add_command(self.menu_copiar)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.menu_copiar.name, type=self.menu_copiar.type)

    async def _checar(self, interaction: discord.Interaction) -> bool:
        if not await requer_permissao(interaction, "create_expressions", "esse comando"):
            return False
        if not interaction.app_permissions.create_expressions:
            await interaction.response.send_message(
                "Eu preciso da permissao Criar Expressoes para isso.", ephemeral=True
            )
            return False
        return True

    @emoji.command(name="adicionar", description="Cria um emoji a partir de uma imagem")
    @app_commands.describe(nome="Nome do emoji (letras, numeros e _)", imagem="PNG, JPG, GIF ou WEBP de ate 256 KB")
    async def adicionar(self, interaction: discord.Interaction, nome: str, imagem: discord.Attachment):
        if not await self._checar(interaction):
            return
        nome_ok = nome_valido(nome)
        if nome_ok is None:
            await interaction.response.send_message("O nome precisa ter pelo menos 2 letras.", ephemeral=True)
            return
        if not (imagem.content_type or "").startswith("image/") or imagem.size > TAMANHO_MAX_EMOJI:
            await interaction.response.send_message("Envie uma imagem de ate 256 KB.", ephemeral=True)
            return
        animado = imagem.content_type == "image/gif"
        if await _vagas_emoji(interaction.guild, animado) <= 0:
            await interaction.response.send_message("O servidor nao tem mais vagas de emoji desse tipo.", ephemeral=True)
            return
        await interaction.response.defer()
        criado = await interaction.guild.create_custom_emoji(
            name=nome_ok, image=await imagem.read(), reason=f"Adicionado por {interaction.user}"
        )
        await interaction.followup.send(f"Emoji criado: {criado} `:{criado.name}:`")

    @emoji.command(name="copiar", description="Copia um emoji de outro servidor (cole o emoji aqui)")
    @app_commands.describe(emoji="O emoji personalizado (ex: :gatinho: de outro servidor)", nome="Nome novo (opcional)")
    async def copiar(self, interaction: discord.Interaction, emoji: str, nome: str | None = None):
        if not await self._checar(interaction):
            return
        encontrados = emojis_no_texto(emoji)
        if not encontrados:
            await interaction.response.send_message(
                "Nao achei um emoji personalizado. Cole o emoji (nao o nome) - emojis padrao do Discord nao precisam ser copiados.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        original = encontrados[0]
        criado = await interaction.guild.create_custom_emoji(
            name=nome_valido(nome or "") or original.name,
            image=await original.read(),
            reason=f"Copiado por {interaction.user}",
        )
        await interaction.followup.send(f"Emoji copiado: {criado} `:{criado.name}:`")

    @emoji.command(name="figurinha", description="Cria uma figurinha a partir de uma imagem")
    @app_commands.describe(
        nome="Nome da figurinha",
        imagem="PNG, APNG ou GIF de ate 512 KB (320x320 fica melhor)",
        emoji_relacionado="Um emoji comum que combina com a figurinha (ex: 😂)",
        descricao="Descricao (opcional)",
    )
    async def figurinha(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 2, 30],
        imagem: discord.Attachment,
        emoji_relacionado: app_commands.Range[str, 1, 32],
        descricao: app_commands.Range[str, 2, 100] | None = None,
    ):
        if not await self._checar(interaction):
            return
        if imagem.content_type not in ("image/png", "image/apng", "image/gif") or imagem.size > TAMANHO_MAX_FIGURINHA:
            await interaction.response.send_message("Envie um PNG, APNG ou GIF de ate 512 KB.", ephemeral=True)
            return
        if len(interaction.guild.stickers) >= interaction.guild.sticker_limit:
            await interaction.response.send_message("O servidor nao tem mais vagas de figurinha.", ephemeral=True)
            return
        await interaction.response.defer()
        criada = await interaction.guild.create_sticker(
            name=nome,
            description=descricao or nome,
            emoji=emoji_relacionado,
            file=await imagem.to_file(),
            reason=f"Adicionada por {interaction.user}",
        )
        await interaction.followup.send(f"Figurinha **{criada.name}** criada!", stickers=[criada])

    @app_commands.guild_only()
    @app_commands.default_permissions(create_expressions=True)
    async def copiar_da_mensagem(self, interaction: discord.Interaction, mensagem: discord.Message):
        if not await self._checar(interaction):
            return
        encontrados = emojis_no_texto(mensagem.content)
        ja_tem = {e.id for e in interaction.guild.emojis}
        novos = [e for e in encontrados if e.id not in ja_tem][:5]
        if not novos:
            await interaction.response.send_message(
                "Essa mensagem nao tem emojis personalizados de outro servidor.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        criados, falhas = [], []
        for original in novos:
            if await _vagas_emoji(interaction.guild, original.animated) <= 0:
                falhas.append(f"{original.name} (sem vaga)")
                continue
            try:
                criados.append(
                    await interaction.guild.create_custom_emoji(
                        name=original.name, image=await original.read(), reason=f"Copiado por {interaction.user}"
                    )
                )
            except discord.HTTPException as erro:
                falhas.append(f"{original.name} ({erro.text or erro.status})")
        texto = ("Adicionados: " + " ".join(str(e) for e in criados)) if criados else "Nenhum emoji adicionado."
        if falhas:
            texto += "\nFalharam: " + ", ".join(falhas)
        await interaction.followup.send(texto, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Expressoes(bot))
