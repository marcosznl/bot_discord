import asyncio
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Awaitable, Callable

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import BASE_DIR, YOUTUBE_DOWNLOAD_DIR, YOUTUBE_VERIFICAR_INTERVALO_MINUTOS
from bot.jsonstore import escrever_json, ler_json
from bot.permissions import requer_usuario_autorizado
from bot.retry_ui import ModalComRetry, ViewComRetry

logger = logging.getLogger(__name__)

# Config e 100% pessoal (por usuario do Discord, guardada em
# YOUTUBE_CONFIG_PATH) - sem nocao de servidor/canal de avisos: o aviso de
# video novo sempre vai direto na DM de quem seguiu aquele canal.
YOUTUBE_CONFIG_PATH = os.path.join(BASE_DIR, "youtube.json")

_ATOM_NS = "http://www.w3.org/2005/Atom"
_YT_NS = "http://www.youtube.com/xml/schemas/2015"
_MEDIA_NS = "http://search.yahoo.com/mrss/"
# Uma pagina de canal do YouTube tem dezenas de IDs "UCxxxx" espalhados pelo
# HTML (canais recomendados, videos relacionados etc.) - um regex solto pega
# o primeiro que aparecer, quase nunca o do proprio canal da pagina. O link
# canonico (e o og:url, mesmo valor) e que aponta pro canal certo.
_CHANNEL_ID_RE = re.compile(
    r'(?:<link rel="canonical" href="|<meta property="og:url" content=")'
    r"https://www\.youtube\.com/channel/(UC[\w-]{22})"
)

# Opcoes de download de video (usadas tanto pelo /baixarvideo quanto pelo
# aviso automatico) - baixa direto em Python via yt-dlp, sem depender de
# nenhum atalho do macOS. "noplaylist" evita repetir o incidente de baixar o
# canal inteiro por engano. "outtmpl" e definido por download (ver
# _youtube_pasta_destino), nao aqui.
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


def _youtube_config_bruta() -> dict:
    return ler_json(YOUTUBE_CONFIG_PATH)


def _youtube_config(user_id: int) -> dict:
    return _youtube_config_bruta().get(str(user_id), {})


def _youtube_adicionar_canal(
    user_id: int, channel_id: str, nome: str, ultimo_video_id: str | None, pasta: str = ""
) -> None:
    dados = _youtube_config_bruta()
    canais = dados.setdefault(str(user_id), {}).setdefault("canais", {})
    canais[channel_id] = {"nome": nome, "ultimo_video_id": ultimo_video_id, "pasta": pasta}
    escrever_json(YOUTUBE_CONFIG_PATH, dados)


def _youtube_remover_canal(user_id: int, channel_id: str) -> None:
    dados = _youtube_config_bruta()
    canais = dados.get(str(user_id), {}).get("canais", {})
    if channel_id not in canais:
        return
    del canais[channel_id]
    escrever_json(YOUTUBE_CONFIG_PATH, dados)


def _youtube_atualizar_ultimo_video(user_id: int, channel_id: str, video_id: str) -> None:
    dados = _youtube_config_bruta()
    canais = dados.get(str(user_id), {}).get("canais", {})
    if channel_id in canais:
        canais[channel_id]["ultimo_video_id"] = video_id
        escrever_json(YOUTUBE_CONFIG_PATH, dados)


def _youtube_extrair_channel_id(html: str) -> str | None:
    """A pagina de um canal do YouTube (por @handle, /c/, /user/ etc.) sempre
    tem um link canonico apontando pro ID real (UCxxxx...) do canal."""
    casamento = _CHANNEL_ID_RE.search(html)
    return casamento.group(1) if casamento else None


def _youtube_parsear_feed(texto: str) -> tuple[str, list[dict]] | None:
    """Le o XML do feed RSS publico do canal (nao precisa de API key/quota) e
    devolve o nome do canal e os videos, do mais recente pro mais antigo."""
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError:
        return None

    elemento_titulo = raiz.find(f"{{{_ATOM_NS}}}title")
    nome_canal = elemento_titulo.text if elemento_titulo is not None else None

    videos = []
    for entrada in raiz.findall(f"{{{_ATOM_NS}}}entry"):
        video_id_el = entrada.find(f"{{{_YT_NS}}}videoId")
        titulo_el = entrada.find(f"{{{_ATOM_NS}}}title")
        link_el = entrada.find(f"{{{_ATOM_NS}}}link")
        if video_id_el is None or titulo_el is None or link_el is None:
            continue

        thumbnail_el = entrada.find(f"{{{_MEDIA_NS}}}group/{{{_MEDIA_NS}}}thumbnail")
        thumbnail = (
            thumbnail_el.attrib["url"]
            if thumbnail_el is not None and "url" in thumbnail_el.attrib
            else f"https://i.ytimg.com/vi/{video_id_el.text}/hqdefault.jpg"
        )

        videos.append(
            {
                "video_id": video_id_el.text,
                "titulo": titulo_el.text,
                "link": link_el.attrib.get("href", f"https://www.youtube.com/watch?v={video_id_el.text}"),
                "thumbnail": thumbnail,
            }
        )

    return nome_canal, videos


# O YouTube costuma devolver 404/500 passageiros nessas paginas/feeds (o mesmo
# pedido segundos depois funciona normalmente) - algumas tentativas com uma
# pequena espera evita depender de "tenta de novo na mao".
YOUTUBE_HTTP_TENTATIVAS = 3
YOUTUBE_HTTP_ESPERA_SEGUNDOS = 1.5


async def _youtube_get_com_retry(
    sessao: aiohttp.ClientSession, url: str, headers: dict | None = None
) -> tuple[int, str | None]:
    ultimo_status = 0
    for tentativa in range(YOUTUBE_HTTP_TENTATIVAS):
        async with sessao.get(url, headers=headers) as resposta:
            if resposta.status == 200:
                return resposta.status, await resposta.text()
            ultimo_status = resposta.status
        if tentativa < YOUTUBE_HTTP_TENTATIVAS - 1:
            await asyncio.sleep(YOUTUBE_HTTP_ESPERA_SEGUNDOS)
    return ultimo_status, None


async def _youtube_resolver_canal_id(sessao: aiohttp.ClientSession, entrada: str) -> str | None:
    """Aceita um ID de canal direto, um @handle ou qualquer link de canal do
    YouTube e devolve o ID (UCxxxx...) - precisa desse ID pro feed RSS."""
    entrada = entrada.strip()
    if re.fullmatch(r"UC[\w-]{22}", entrada):
        return entrada

    if entrada.startswith("http://") or entrada.startswith("https://"):
        url = entrada
    elif entrada.startswith("@"):
        url = f"https://www.youtube.com/{entrada}"
    else:
        url = f"https://www.youtube.com/@{entrada}"

    try:
        status, html = await _youtube_get_com_retry(sessao, url, headers={"User-Agent": "Mozilla/5.0"})
        if html is None:
            logger.error(f"Pagina do canal do YouTube '{entrada}' devolveu status {status} ({url})")
            return None
    except aiohttp.ClientError as erro:
        logger.error(f"Erro ao resolver canal do YouTube '{entrada}': {erro}")
        return None

    channel_id = _youtube_extrair_channel_id(html)
    if channel_id is None:
        logger.error(f"Nao encontrei o link canonico do canal na pagina de '{entrada}' ({url})")
    return channel_id


async def _youtube_buscar_feed(sessao: aiohttp.ClientSession, channel_id: str) -> tuple[str, list[dict]] | None:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        status, texto = await _youtube_get_com_retry(sessao, url)
        if texto is None:
            logger.error(f"Feed do canal do YouTube {channel_id} devolveu status {status}")
            return None
    except aiohttp.ClientError as erro:
        logger.error(f"Erro ao buscar feed do canal do YouTube {channel_id}: {erro}")
        return None

    resultado = _youtube_parsear_feed(texto)
    if resultado is None:
        logger.error(f"Feed invalido para o canal do YouTube {channel_id}")
        return None

    nome_canal, videos = resultado
    return nome_canal or channel_id, videos


def _youtube_pastas_disponiveis() -> list[str]:
    """Subpastas dentro de YOUTUBE_DOWNLOAD_DIR (ex: uma por pessoa),
    oferecidas como destino ao adicionar um canal - le o disco na hora, e so
    uma listagem simples de diretorio, sem custo real."""
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


def _youtube_baixar_video(link: str, progress_hook, pasta: str = "") -> str:
    """Baixa o video de fato via yt-dlp (bloqueante - so chamar via
    asyncio.to_thread). progress_hook e chamado pelo proprio yt-dlp, de dentro
    dessa thread, a cada pedaco baixado. Devolve o titulo do video baixado."""
    destino = _youtube_pasta_destino(pasta)
    opts = dict(YTDLP_DOWNLOAD_OPTS, progress_hooks=[progress_hook], outtmpl=os.path.join(destino, "%(title)s.%(ext)s"))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=True)
        return info.get("title", link)


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


class ModalAdicionarCanalYoutube(ModalComRetry):
    def __init__(self, user_id: int):
        super().__init__(title="Adicionar canal do YouTube")
        self.user_id = user_id
        self.entrada = discord.ui.TextInput(
            label="Link, @handle ou ID do canal",
            placeholder="https://www.youtube.com/@nomedocanal",
            max_length=200,
        )
        self.add_item(self.entrada)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        texto = str(self.entrada)

        async with aiohttp.ClientSession() as sessao:
            channel_id = await _youtube_resolver_canal_id(sessao, texto)
            if channel_id is None:
                await interaction.followup.send("Nao consegui encontrar esse canal do YouTube.", ephemeral=True)
                return

            resultado = await _youtube_buscar_feed(sessao, channel_id)

        if resultado is None:
            await interaction.followup.send("Encontrei o canal, mas nao consegui ler os videos dele.", ephemeral=True)
            return

        nome_canal, videos = resultado
        ultimo_video_id = videos[0]["video_id"] if videos else None
        # YOUTUBE_DOWNLOAD_DIR mora no iCloud Drive - listar a pasta pode
        # bloquear de verdade (sincronizacao do iCloud), por isso roda numa
        # thread em vez de travar o loop de eventos do bot.
        pastas_disponiveis = await asyncio.to_thread(_youtube_pastas_disponiveis)
        await interaction.followup.send(
            f"Canal **{nome_canal}** encontrado - em qual pasta salvar os videos baixados dele?",
            view=SelecionarPastaCanalYoutube(
                self.user_id, channel_id, nome_canal, ultimo_video_id, pastas_disponiveis
            ),
            ephemeral=True,
        )


def _youtube_opcoes_pasta(pastas_disponiveis: list[str]) -> list[discord.SelectOption]:
    opcoes = [discord.SelectOption(label="Pasta principal (sem subpasta)", value="_raiz")]
    opcoes += [discord.SelectOption(label=pasta, value=pasta) for pasta in pastas_disponiveis]
    opcoes.append(discord.SelectOption(label="+ Criar nova pasta", value="_nova"))
    return opcoes[:25]


class ModalNovaPastaYoutube(ModalComRetry):
    """Pede o nome de uma pasta nova dentro de YOUTUBE_DOWNLOAD_DIR, cria ela
    e continua o fluxo (salvar canal ou iniciar download) que a chamou."""

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


class SelecionarPastaCanalYoutube(ViewComRetry):
    def __init__(
        self,
        user_id: int,
        channel_id: str,
        nome_canal: str,
        ultimo_video_id: str | None,
        pastas_disponiveis: list[str],
    ):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.channel_id = channel_id
        self.nome_canal = nome_canal
        self.ultimo_video_id = ultimo_video_id

        self.select = discord.ui.Select(
            placeholder="Escolha a pasta de destino", options=_youtube_opcoes_pasta(pastas_disponiveis)
        )
        self.select.callback = self._escolher
        self.add_item(self.select)

    async def _escolher(self, interaction: discord.Interaction):
        valor = self.select.values[0]
        if valor == "_nova":
            await interaction.response.send_modal(ModalNovaPastaYoutube(self._salvar))
            return
        pasta = "" if valor == "_raiz" else valor
        await self._salvar(interaction, pasta)

    async def _salvar(self, interaction: discord.Interaction, pasta: str) -> None:
        await asyncio.to_thread(
            _youtube_adicionar_canal, self.user_id, self.channel_id, self.nome_canal, self.ultimo_video_id, pasta
        )
        destino = f"pasta **{pasta}**" if pasta else "pasta principal"
        await interaction.response.send_message(
            f"Canal **{self.nome_canal}** adicionado (salvando em: {destino}) - "
            "vou avisar sobre os proximos videos dele.",
            ephemeral=True,
        )


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


class SelecionarRemoverCanalYoutube(ViewComRetry):
    def __init__(self, user_id: int, canais: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.select = discord.ui.Select(
            placeholder="Escolha o canal para remover",
            options=[
                discord.SelectOption(label=info.get("nome", canal_id)[:100], value=canal_id)
                for canal_id, info in canais.items()
            ][:25],
        )
        self.select.callback = self._remover
        self.add_item(self.select)

    async def _remover(self, interaction: discord.Interaction):
        canal_id = self.select.values[0]
        await asyncio.to_thread(_youtube_remover_canal, self.user_id, canal_id)
        await interaction.response.send_message("Canal removido dos avisos.", ephemeral=True)


class BotoesYoutube(ViewComRetry):
    def __init__(self, user_id: int):
        super().__init__(timeout=600)
        self.user_id = user_id

    @discord.ui.button(label="Adicionar canal", style=discord.ButtonStyle.success)
    async def adicionar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalAdicionarCanalYoutube(self.user_id))

    @discord.ui.button(label="Remover canal", style=discord.ButtonStyle.danger)
    async def remover(self, interaction: discord.Interaction, botao: discord.ui.Button):
        config = await asyncio.to_thread(_youtube_config, self.user_id)
        canais = config.get("canais", {})
        if not canais:
            await interaction.response.send_message("Nenhum canal configurado ainda.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Escolha o canal para remover:",
            view=SelecionarRemoverCanalYoutube(self.user_id, canais),
            ephemeral=True,
        )

    @discord.ui.button(label="Ver configuracao", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, botao: discord.ui.Button):
        config = await asyncio.to_thread(_youtube_config, self.user_id)
        canais = config.get("canais", {})

        if not canais:
            await interaction.response.send_message("Nenhum canal do YouTube adicionado ainda.", ephemeral=True)
            return

        def _linha_canal(canal_id: str, info: dict) -> str:
            nome = info.get("nome", canal_id)
            pasta = info.get("pasta")
            return f"- {nome} (pasta: {pasta})" if pasta else f"- {nome}"

        lista = "\n".join(_linha_canal(canal_id, info) for canal_id, info in canais.items())
        await interaction.response.send_message(
            f"Avisos chegam na sua DM. Canais acompanhados:\n{lista}", ephemeral=True
        )


class Youtube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.verificar_novos_videos.cancel()

    @tasks.loop(minutes=YOUTUBE_VERIFICAR_INTERVALO_MINUTOS)
    async def verificar_novos_videos(self):
        dados = await asyncio.to_thread(_youtube_config_bruta)
        if not dados:
            return

        async with aiohttp.ClientSession() as sessao:
            for user_id_str, config in dados.items():
                canais = config.get("canais", {})
                if not canais:
                    continue

                usuario = self.bot.get_user(int(user_id_str))
                if usuario is None:
                    try:
                        usuario = await self.bot.fetch_user(int(user_id_str))
                    except discord.NotFound:
                        logger.error(f"Usuario do Discord {user_id_str} nao encontrado - pulando avisos dele.")
                        continue

                for channel_id, info in list(canais.items()):
                    try:
                        resultado = await _youtube_buscar_feed(sessao, channel_id)
                    except Exception as erro:
                        logger.error(f"Erro ao verificar canal do YouTube {channel_id}: {erro}")
                        continue
                    if resultado is None or not resultado[1]:
                        continue

                    nome_canal, videos = resultado
                    ultimo_conhecido = info.get("ultimo_video_id")
                    video_mais_recente = videos[0]
                    if video_mais_recente["video_id"] == ultimo_conhecido:
                        continue

                    if ultimo_conhecido is None:
                        # Nunca teve um "ultimo video" salvo pra esse canal (nao
                        # deveria acontecer via /youtube, mas evita floodar o canal
                        # com o historico inteiro caso aconteca) - avisa so do
                        # mais recente.
                        novos = [video_mais_recente]
                    else:
                        novos = []
                        encontrado = False
                        for video in videos:
                            if video["video_id"] == ultimo_conhecido:
                                encontrado = True
                                break
                            novos.append(video)

                        if encontrado:
                            novos.reverse()
                        else:
                            # Saiu mais video do que o feed guarda desde a ultima
                            # checagem (ex: bot ficou muito tempo offline) - avisa
                            # so do mais recente pra nao inundar o canal com o
                            # historico inteiro.
                            novos = [video_mais_recente]

                    for video in novos:
                        embed = discord.Embed(
                            title=video["titulo"],
                            url=video["link"],
                            description=f"Novo video de **{nome_canal}**",
                            color=discord.Color.red(),
                        )
                        embed.set_image(url=video["thumbnail"])
                        try:
                            await usuario.send(embed=embed)
                        except (discord.Forbidden, discord.HTTPException) as erro:
                            # DM fechada pra bots/membros do servidor, bloqueou o bot etc.
                            logger.error(f"Nao consegui mandar DM pra {user_id_str}: {erro}")
                            continue

                        try:
                            await asyncio.to_thread(
                                _youtube_baixar_video, video["link"], lambda d: None, info.get("pasta", "")
                            )
                        except Exception as erro:
                            logger.error(f"Erro ao baixar video automatico {video['link']}: {erro}")

                    await asyncio.to_thread(
                        _youtube_atualizar_ultimo_video,
                        int(user_id_str),
                        channel_id,
                        video_mais_recente["video_id"],
                    )

    @app_commands.command(name="youtube", description="Configura seus avisos de novos videos de canais do YouTube")
    # So DM/chat privado - nao aparece em nenhum servidor (comando pessoal).
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=False, users=True)
    async def youtube(self, interaction: discord.Interaction):
        if not await requer_usuario_autorizado(interaction, "esse comando"):
            return

        await interaction.response.send_message(
            "Gerencie os canais do YouTube que voce acompanha (os avisos chegam na sua DM):",
            view=BotoesYoutube(interaction.user.id),
            ephemeral=True,
        )

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
