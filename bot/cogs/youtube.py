import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from bot.config import YOUTUBE_DOWNLOAD_DIR
from bot.permissions import requer_usuario_autorizado
from bot.retry_ui import ModalComRetry, ViewComRetry

logger = logging.getLogger(__name__)

# Opcoes de download de video (usadas por /baixarvideo) - baixa direto em
# Python via yt-dlp, sem depender de nenhum atalho do macOS. "noplaylist"
# evita repetir o incidente de baixar o canal inteiro por engano. "outtmpl" e
# definido por download (ver _youtube_pasta_destino), nao aqui.
# "bv*+ba/b" (em vez de so "bestvideo+bestaudio/best") garante pegar a maior
# resolucao/bitrate disponivel mesmo quando isso exige juntar formatos que o
# seletor "bv" sozinho descartaria. Sem restringir "player_client": forcar
# so web+android fazia o YouTube devolver so formatos baixos (chegava a cair
# pra 360p) por causa do experimento SABR-only da Google nesses clients -
# deixando o yt-dlp escolher os clients (padrao dele) e que realmente traz a
# qualidade maxima real do video (testado: ate 4K quando o video tem).
YTDLP_DOWNLOAD_OPTS = {
    "format": "bv*+ba/b",
    "merge_output_format": "mp4",
    "noplaylist": True,
    "quiet": True,
}
# Reserva usada quando o download na qualidade maxima falha no meio do
# caminho (HTTP 403 - a URL assinada de formatos grandes/4K pode expirar ou
# ser bloqueada antes do download terminar). Limitar a 1080p reduz bastante
# essa chance; melhor entregar o video numa qualidade um pouco menor do que
# nao entregar nada.
YTDLP_DOWNLOAD_OPTS_RESERVA = dict(YTDLP_DOWNLOAD_OPTS, format="bv*[height<=1080]+ba/b[height<=1080]")
# Mesma ideia, mas so audio - usada pelo /baixaraudio. O ffmpeg que extrai o
# MP3 e o mesmo que ja faz a mesclagem de video+audio acima.
YTDLP_DOWNLOAD_AUDIO_OPTS = {
    "format": "ba/b",
    "noplaylist": True,
    "quiet": True,
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
}
# Passo de progresso do /baixarvideo e intervalo minimo entre edicoes da
# mensagem - o hook do yt-dlp dispara varias vezes por segundo, editar a cada
# tick estouraria o rate limit do Discord (e nao ajudaria em nada de qualquer
# jeito, e rapido demais pra ler).
BAIXARVIDEO_PASSO_PERCENTUAL = 10
BAIXARVIDEO_INTERVALO_MINIMO_SEGUNDOS = 2.0


def _youtube_pastas_disponiveis() -> list[str]:
    """Subpastas dentro de YOUTUBE_DOWNLOAD_DIR (ex: uma por pessoa),
    oferecidas como destino ao baixar um video - le o disco na hora, e so uma
    listagem simples de diretorio, sem custo real."""
    try:
        entradas = os.listdir(YOUTUBE_DOWNLOAD_DIR)
    except OSError:
        return []
    return sorted(
        nome
        for nome in entradas
        if not nome.startswith(".") and os.path.isdir(os.path.join(YOUTUBE_DOWNLOAD_DIR, nome))
    )


def _youtube_pasta_destino(pasta: str) -> str:
    """Caminho final onde salvar o video - YOUTUBE_DOWNLOAD_DIR, ou uma
    subpasta dele. Cria a pasta se ainda nao existir."""
    destino = os.path.join(YOUTUBE_DOWNLOAD_DIR, pasta) if pasta else YOUTUBE_DOWNLOAD_DIR
    os.makedirs(destino, exist_ok=True)
    return destino


def _youtube_nome_pasta_valido(nome: str) -> bool:
    """Nome de pasta seguro pra criar dentro de YOUTUBE_DOWNLOAD_DIR - sem
    separador de caminho nem nomes especiais que possam escapar dela."""
    return bool(nome) and nome not in (".", "..") and "/" not in nome and "\\" not in nome


def _youtube_baixar_com_opts(opts_base: dict, link: str, progress_hook, outtmpl: str) -> str:
    opts = dict(opts_base, progress_hooks=[progress_hook], outtmpl=outtmpl)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=True)
        return info.get("title", link)


def _youtube_baixar_video(link: str, progress_hook, pasta: str = "") -> str:
    """Baixa o video de fato via yt-dlp (bloqueante - so chamar via
    asyncio.to_thread). progress_hook e chamado pelo proprio yt-dlp, de dentro
    dessa thread, a cada pedaco baixado. Devolve o titulo do video baixado.
    Se a qualidade maxima falhar no meio do download, tenta de novo uma vez
    limitado a 1080p (ver YTDLP_DOWNLOAD_OPTS_RESERVA) antes de desistir."""
    destino = _youtube_pasta_destino(pasta)
    outtmpl = os.path.join(destino, "%(title)s.%(ext)s")
    try:
        return _youtube_baixar_com_opts(YTDLP_DOWNLOAD_OPTS, link, progress_hook, outtmpl)
    except yt_dlp.utils.DownloadError:
        logger.warning(f"Falha baixando {link} na qualidade maxima, tentando de novo limitado a 1080p")
        return _youtube_baixar_com_opts(YTDLP_DOWNLOAD_OPTS_RESERVA, link, progress_hook, outtmpl)


def _youtube_baixar_audio(link: str, progress_hook, pasta: str = "") -> str:
    """Mesma ideia de _youtube_baixar_video, mas extrai so o audio em MP3 -
    usado pelo /baixaraudio."""
    destino = _youtube_pasta_destino(pasta)
    opts = dict(
        YTDLP_DOWNLOAD_AUDIO_OPTS,
        progress_hooks=[progress_hook],
        outtmpl=os.path.join(destino, "%(title)s.%(ext)s"),
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=True)
        return info.get("title", link)


def _youtube_opcoes_pasta(pastas_disponiveis: list[str]) -> list[discord.SelectOption]:
    opcoes = [discord.SelectOption(label="Pasta principal (sem subpasta)", value="_raiz")]
    opcoes += [discord.SelectOption(label=pasta, value=pasta) for pasta in pastas_disponiveis]
    opcoes.append(discord.SelectOption(label="+ Criar nova pasta", value="_nova"))
    return opcoes[:25]


class ModalNovaPastaYoutube(ModalComRetry):
    """Pede o nome de uma pasta nova dentro de YOUTUBE_DOWNLOAD_DIR, cria ela
    e continua o fluxo (iniciar download) que a chamou."""

    def __init__(self, continuar: Callable[[discord.Interaction, str], Awaitable[None]]):
        super().__init__(title="Nova pasta")
        self.continuar = continuar
        self.nome = discord.ui.TextInput(label="Nome da pasta", max_length=100)
        self.add_item(self.nome)

    async def on_submit(self, interaction: discord.Interaction):
        nome = str(self.nome).strip()
        if not _youtube_nome_pasta_valido(nome):
            await interaction.response.send_message(
                "Nome de pasta invalido (nao pode ser vazio nem ter '/' ou '\\').", ephemeral=True
            )
            return
        await asyncio.to_thread(_youtube_pasta_destino, nome)
        await self.continuar(interaction, nome)


async def _baixarvideo_executar(mensagem: discord.Message, link: str, pasta: str, baixar_func=_youtube_baixar_video) -> None:
    """Baixa (video ou audio, conforme baixar_func) e vai editando 'mensagem'
    com o progresso (0/10/20...%) ate o resultado final - usado pelo
    /baixarvideo e /baixaraudio depois que a pasta e escolhida."""
    loop = asyncio.get_running_loop()
    estado = {"bucket": 0, "ultima_edicao": 0.0}

    def progress_hook(d):
        if d.get("status") != "downloading":
            return
        baixados = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if not total:
            return
        percentual = int(baixados / total * 100)
        # Video e audio baixam em passadas separadas (formato
        # bestvideo+bestaudio) - cada uma reinicia do 0%, entao uma queda
        # grande indica que comecou um novo arquivo.
        if percentual < estado["bucket"] - BAIXARVIDEO_PASSO_PERCENTUAL:
            estado["bucket"] = 0
        bucket = (percentual // BAIXARVIDEO_PASSO_PERCENTUAL) * BAIXARVIDEO_PASSO_PERCENTUAL
        if bucket <= estado["bucket"]:
            return
        agora = time.monotonic()
        if agora - estado["ultima_edicao"] < BAIXARVIDEO_INTERVALO_MINIMO_SEGUNDOS:
            return
        estado["bucket"] = bucket
        estado["ultima_edicao"] = agora
        asyncio.run_coroutine_threadsafe(mensagem.edit(content=f"Baixando... {bucket}%"), loop)

    try:
        titulo = await asyncio.to_thread(baixar_func, link, progress_hook, pasta)
    except Exception as erro:
        logger.error(f"Erro ao baixar '{link}': {erro}")
        await mensagem.edit(content=f"Falha ao baixar: {erro}")
        return

    await mensagem.edit(content=f"Baixado com sucesso: **{titulo}**")


class SelecionarPastaBaixar(ViewComRetry):
    """Usado tanto por /baixarvideo quanto por /baixaraudio - so muda qual
    funcao de download roda depois que a pasta e escolhida."""

    def __init__(self, link: str, pastas_disponiveis: list[str], baixar_func=_youtube_baixar_video):
        super().__init__(timeout=300)
        self.link = link
        self.baixar_func = baixar_func
        self.mensagem: discord.Message | None = None
        self.select = discord.ui.Select(
            placeholder="Escolha a pasta de destino", options=_youtube_opcoes_pasta(pastas_disponiveis)
        )
        self.select.callback = self._escolher
        self.add_item(self.select)

    async def _escolher(self, interaction: discord.Interaction):
        valor = self.select.values[0]
        if valor == "_nova":
            # Guarda a mensagem original agora - a interacao do modal, la na
            # frente, nao esta ligada a essa mensagem pra poder editar direto.
            self.mensagem = interaction.message
            await interaction.response.send_modal(ModalNovaPastaYoutube(self._pasta_criada_pelo_modal))
            return

        pasta = "" if valor == "_raiz" else valor
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Baixando... 0%", view=self)
        await _baixarvideo_executar(interaction.message, self.link, pasta, self.baixar_func)

    async def _pasta_criada_pelo_modal(self, interaction: discord.Interaction, pasta: str) -> None:
        await interaction.response.send_message(f"Pasta **{pasta}** criada - baixando...", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await self.mensagem.edit(content="Baixando... 0%", view=self)
        await _baixarvideo_executar(self.mensagem, self.link, pasta, self.baixar_func)


class Youtube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="baixarvideo", description="Baixa o video de um link, mostrando o progresso")
    @app_commands.describe(link="Link do video (ex: YouTube) para baixar")
    # So DM/chat privado - nao aparece em nenhum servidor (comando pessoal).
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=False, users=True)
    async def baixarvideo(self, interaction: discord.Interaction, link: str):
        if not await requer_usuario_autorizado(interaction, "esse comando"):
            return

        link = link.strip()
        if not link.startswith("http://") and not link.startswith("https://"):
            await interaction.response.send_message("Isso nao parece um link valido.", ephemeral=True)
            return

        await interaction.response.defer()
        # YOUTUBE_DOWNLOAD_DIR mora no iCloud Drive - listar a pasta pode
        # bloquear de verdade (sincronizacao do iCloud), por isso roda numa
        # thread em vez de travar o loop de eventos do bot.
        pastas_disponiveis = await asyncio.to_thread(_youtube_pastas_disponiveis)
        await interaction.followup.send(
            "Em qual pasta salvar esse video?",
            view=SelecionarPastaBaixar(link, pastas_disponiveis, _youtube_baixar_video),
        )

    @app_commands.command(name="baixaraudio", description="Baixa so o audio (MP3) de um link, mostrando o progresso")
    @app_commands.describe(link="Link do video (ex: YouTube) para baixar o audio")
    # So DM/chat privado - nao aparece em nenhum servidor (comando pessoal).
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=False, users=True)
    async def baixaraudio(self, interaction: discord.Interaction, link: str):
        if not await requer_usuario_autorizado(interaction, "esse comando"):
            return

        link = link.strip()
        if not link.startswith("http://") and not link.startswith("https://"):
            await interaction.response.send_message("Isso nao parece um link valido.", ephemeral=True)
            return

        await interaction.response.defer()
        pastas_disponiveis = await asyncio.to_thread(_youtube_pastas_disponiveis)
        await interaction.followup.send(
            "Em qual pasta salvar esse audio?",
            view=SelecionarPastaBaixar(link, pastas_disponiveis, _youtube_baixar_audio),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Youtube(bot))
