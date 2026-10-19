import asyncio
import logging

import discord
from discord.ext import commands

from bot import banco, configstore, layout

logger = logging.getLogger(__name__)

# {"canal": id, "minimo": int, "emoji": str}
CHAVE_CONFIG = "destaques"
EMOJI_PADRAO = "⭐"

banco.registrar_schema(
    "CREATE TABLE IF NOT EXISTS destaques ("
    "mensagem_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, destaque_id INTEGER NOT NULL)"
)


def destaque_obter(mensagem_id: int) -> int | None:
    linha = banco.um("SELECT destaque_id FROM destaques WHERE mensagem_id = ?", (mensagem_id,))
    return linha[0] if linha else None


def destaque_salvar(mensagem_id: int, guild_id: int, destaque_id: int) -> None:
    banco.executar(
        "INSERT INTO destaques (mensagem_id, guild_id, destaque_id) VALUES (?, ?, ?) "
        "ON CONFLICT (mensagem_id) DO UPDATE SET destaque_id = excluded.destaque_id",
        (mensagem_id, guild_id, destaque_id),
    )


def destaque_remover(mensagem_id: int) -> None:
    banco.executar("DELETE FROM destaques WHERE mensagem_id = ?", (mensagem_id,))


def cartao_destaque(mensagem: discord.Message, emoji: str, quantidade: int) -> discord.ui.LayoutView:
    autor = mensagem.author
    texto = mensagem.content or ("(so anexo)" if mensagem.attachments else "(sem texto)")
    itens: list[discord.ui.Item] = [
        layout.secao_com_miniatura(f"**{autor.display_name}**\n{texto[:3000]}", autor.display_avatar.url)
    ]
    imagens = [anexo.url for anexo in mensagem.attachments if (anexo.content_type or "").startswith("image/")][:10]
    if imagens:
        itens.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in imagens)))
    itens.append(layout.separador())
    itens.append(
        discord.ui.Section(
            f"{emoji} **{quantidade}** em {mensagem.channel.mention}",
            accessory=discord.ui.Button(label="Ir para a mensagem", url=mensagem.jump_url),
        )
    )
    return layout.view_estatica(*itens, cor=discord.Color.gold())


class Destaques(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Varias reacoes quase juntas na mesma mensagem poderiam criar dois
        # destaques - um lock por mensagem evita isso.
        self._locks: dict[int, asyncio.Lock] = {}

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._atualizar(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._atualizar(payload)

    async def _atualizar(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        config = await asyncio.to_thread(configstore.obter, payload.guild_id, CHAVE_CONFIG)
        if not config:
            return
        emoji = config.get("emoji") or EMOJI_PADRAO
        if str(payload.emoji) != emoji or payload.channel_id == config["canal"]:
            return
        guild = self.bot.get_guild(payload.guild_id)
        mural = guild.get_channel(config["canal"]) if guild else None
        origem = guild.get_channel_or_thread(payload.channel_id) if guild else None
        if mural is None or origem is None:
            return
        # Nao leva conteudo de canal +18 para um mural que nao e.
        if getattr(origem, "nsfw", False) and not getattr(mural, "nsfw", False):
            return

        lock = self._locks.setdefault(payload.message_id, asyncio.Lock())
        async with lock:
            try:
                mensagem = await origem.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if mensagem.author.bot:
                return
            reacao = discord.utils.find(lambda r: str(r.emoji) == emoji, mensagem.reactions)
            quantidade = reacao.count if reacao else 0
            destaque_id = await asyncio.to_thread(destaque_obter, mensagem.id)

            try:
                if quantidade >= config.get("minimo", 3):
                    view = cartao_destaque(mensagem, emoji, quantidade)
                    if destaque_id:
                        await mural.get_partial_message(destaque_id).edit(view=view)
                    else:
                        enviado = await mural.send(view=view, allowed_mentions=discord.AllowedMentions.none())
                        await asyncio.to_thread(destaque_salvar, mensagem.id, guild.id, enviado.id)
                elif destaque_id:
                    await mural.get_partial_message(destaque_id).delete()
                    await asyncio.to_thread(destaque_remover, mensagem.id)
            except discord.NotFound:
                # Alguem apagou o destaque a mao - esquece e deixa recriar.
                await asyncio.to_thread(destaque_remover, mensagem.id)
            except discord.HTTPException as erro:
                logger.warning(f"Erro no mural de destaques de {guild.id}: {erro}")
        if not lock.locked():
            self._locks.pop(payload.message_id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Destaques(bot))
