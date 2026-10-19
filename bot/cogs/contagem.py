import asyncio
from dataclasses import dataclass

import discord
from discord.ext import commands

from bot import banco, configstore

CHAVE_CANAL = "contagem_canal"

banco.registrar_schema(
    "CREATE TABLE IF NOT EXISTS contagem ("
    "guild_id INTEGER PRIMARY KEY, atual INTEGER NOT NULL DEFAULT 0, ultimo_user INTEGER, recorde INTEGER NOT NULL DEFAULT 0)"
)


@dataclass
class EstadoContagem:
    atual: int = 0
    ultimo_user: int | None = None
    recorde: int = 0


def avaliar(estado: EstadoContagem, user_id: int, numero: int) -> tuple[str, EstadoContagem]:
    """Devolve ("ok" | "recorde" | "numero_errado" | "repetiu", novo estado).
    Errar zera a contagem; o recorde fica."""
    if user_id == estado.ultimo_user:
        return "repetiu", EstadoContagem(0, None, estado.recorde)
    if numero != estado.atual + 1:
        return "numero_errado", EstadoContagem(0, None, estado.recorde)
    novo_recorde = numero > estado.recorde
    novo = EstadoContagem(numero, user_id, max(numero, estado.recorde))
    return ("recorde" if novo_recorde and numero > 1 else "ok"), novo


def contagem_obter(guild_id: int) -> EstadoContagem:
    linha = banco.um("SELECT atual, ultimo_user, recorde FROM contagem WHERE guild_id = ?", (guild_id,))
    return EstadoContagem(*linha) if linha else EstadoContagem()


def contagem_salvar(guild_id: int, estado: EstadoContagem) -> None:
    banco.executar(
        "INSERT INTO contagem (guild_id, atual, ultimo_user, recorde) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (guild_id) DO UPDATE SET atual = excluded.atual, ultimo_user = excluded.ultimo_user, "
        "recorde = excluded.recorde",
        (guild_id, estado.atual, estado.ultimo_user, estado.recorde),
    )


class Contagem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}
        # Canal de contagem por servidor, em memoria - on_message roda em
        # TODA mensagem, nao vale ir no banco cada vez.
        self._canais: dict[int, int | None] = {}

    def esquecer_canal(self, guild_id: int) -> None:
        """Chamado pelo /configurar quando o canal muda."""
        self._canais.pop(guild_id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        guild_id = message.guild.id
        if guild_id not in self._canais:
            self._canais[guild_id] = await asyncio.to_thread(configstore.obter, guild_id, CHAVE_CANAL)
        if message.channel.id != self._canais[guild_id]:
            return
        texto = message.content.strip()
        if not texto.isdigit():
            return  # conversa normal no canal nao conta

        async with self._locks.setdefault(guild_id, asyncio.Lock()):
            estado = await asyncio.to_thread(contagem_obter, guild_id)
            resultado, novo = avaliar(estado, message.author.id, int(texto))
            await asyncio.to_thread(contagem_salvar, guild_id, novo)

        if resultado == "ok":
            await message.add_reaction("✅")
        elif resultado == "recorde":
            await message.add_reaction("🏆")
        else:
            await message.add_reaction("❌")
            motivo = (
                "contou duas vezes seguidas" if resultado == "repetiu" else f"errou - o proximo era **{estado.atual + 1}**"
            )
            await message.channel.send(
                f"{message.author.mention} {motivo}! A contagem parou em **{estado.atual}** "
                f"(recorde: {novo.recorde}). Recomecem do **1**."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Contagem(bot))
