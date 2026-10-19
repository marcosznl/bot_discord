import asyncio
import datetime
import glob
import itertools
import logging
import os
import sqlite3
import tempfile
import zipfile

import discord
from discord.ext import commands, tasks

from bot.config import BASE_DIR, DATA_DIR, FUSO

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUPS_MANTIDOS = 7
HORA_DO_BACKUP = 4
EXTRAS_BACKUP = [os.path.join(BASE_DIR, "boas_vindas.json")]


def fazer_backup(dados: str, destino: str, extras: list[str], manter: int, hoje: datetime.date) -> str:
    """Copia os bancos (pela API de backup do sqlite, que funciona com o
    bot rodando) e os JSONs num zip datado. Apaga os zips mais antigos."""
    os.makedirs(destino, exist_ok=True)
    caminho_zip = os.path.join(destino, f"backup-{hoje.isoformat()}.zip")
    with tempfile.TemporaryDirectory() as temporario, zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as arquivo_zip:
        for banco in sorted(glob.glob(os.path.join(dados, "*.db"))):
            copia = os.path.join(temporario, os.path.basename(banco))
            origem, alvo = sqlite3.connect(banco), sqlite3.connect(copia)
            try:
                origem.backup(alvo)
            finally:
                alvo.close()
                origem.close()
            arquivo_zip.write(copia, f"data/{os.path.basename(banco)}")
        for json_dados in sorted(glob.glob(os.path.join(dados, "*.json"))):
            arquivo_zip.write(json_dados, f"data/{os.path.basename(json_dados)}")
        pasta_anuncios = os.path.join(dados, "anuncios")
        for imagem in sorted(glob.glob(os.path.join(pasta_anuncios, "*"))):
            arquivo_zip.write(imagem, f"data/anuncios/{os.path.basename(imagem)}")
        for extra in extras:
            if os.path.exists(extra):
                arquivo_zip.write(extra, os.path.basename(extra))

    antigos = sorted(glob.glob(os.path.join(destino, "backup-*.zip")))[:-manter]
    for velho in antigos:
        os.remove(velho)
    return caminho_zip


class Manutencao(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultimo_backup: datetime.date | None = None
        self._status = itertools.cycle(range(4))

    async def cog_load(self) -> None:
        self.backup_diario.start()
        self.girar_status.start()

    async def cog_unload(self) -> None:
        self.backup_diario.cancel()
        self.girar_status.cancel()

    @tasks.loop(minutes=20)
    async def backup_diario(self):
        agora = datetime.datetime.now(FUSO)
        if agora.hour != HORA_DO_BACKUP or self._ultimo_backup == agora.date():
            return
        self._ultimo_backup = agora.date()
        try:
            caminho = await asyncio.to_thread(
                fazer_backup, DATA_DIR, BACKUP_DIR, EXTRAS_BACKUP, BACKUPS_MANTIDOS, agora.date()
            )
            logger.info(f"Backup feito: {caminho}")
        except (OSError, sqlite3.Error, zipfile.BadZipFile):
            logger.exception("Falha no backup diario")

    def _proximo_status(self) -> discord.BaseActivity:
        # Se tiver musica tocando em algum servidor, ela aparece primeiro.
        from bot.cogs.musica import _filas_musica

        tocando = next(
            (
                _filas_musica[guild.id][0].titulo
                for guild in self.bot.guilds
                if guild.voice_client is not None and guild.voice_client.is_playing() and _filas_musica.get(guild.id)
            ),
            None,
        )
        if tocando:
            return discord.Activity(type=discord.ActivityType.listening, name=tocando[:120])
        opcao = next(self._status)
        if opcao == 0:
            return discord.Game(name="Dattebayo!")
        if opcao == 1:
            return discord.CustomActivity(name="Digite /ajuda para ver os comandos")
        if opcao == 2:
            membros = sum(guild.member_count or 0 for guild in self.bot.guilds)
            return discord.Activity(type=discord.ActivityType.watching, name=f"{membros} membros")
        return discord.Activity(
            type=discord.ActivityType.watching, name=f"{len(self.bot.guilds)} servidor(es)"
        )

    @tasks.loop(minutes=2)
    async def girar_status(self):
        await self.bot.change_presence(activity=self._proximo_status())

    @backup_diario.before_loop
    @girar_status.before_loop
    async def _antes(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Manutencao(bot))
