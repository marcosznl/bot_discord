import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

from bot import layout
from bot.emojis import emoji
from bot.config import DATA_DIR
from bot.jsonstore import escrever_json, ler_json
from bot.retry_ui import LayoutViewComRetry, ModalComRetry

logger = logging.getLogger(__name__)

YTDLP_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    # O cliente "android_vr" (padrao do yt-dlp) costuma retornar links que o
    # YouTube bloqueia com 403 fora do proprio app. "web" evita isso na maioria
    # dos casos, mas alguns videos so expoem audio completo no "android" -
    # por isso os dois sao tentados, nessa ordem de prioridade.
    "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
}
VOICE_IDLE_TIMEOUT = 300
# Se uma faixa parar em menos que isso, foi provavelmente um link que o
# YouTube bloqueou (403) em vez do fim natural da musica - vale tentar de novo.
FALHA_REPRODUCAO_SEGUNDOS = 2.0



@dataclass
class Faixa:
    titulo: str
    busca: str
    duracao: int | None
    pagina: str | None = None  # link do video, para o painel e para tocar de novo o mesmo video
    miniatura: str | None = None


_filas_musica: dict[int, list[Faixa]] = {}
_historico_musica: dict[int, list[Faixa]] = {}
_inicio_faixa: dict[int, float] = {}
_canal_voz: dict[int, int] = {}

# Fila salva em disco para continuar tocando depois que o bot reinicia.
FILA_PATH = os.path.join(DATA_DIR, "musica_fila.json")


def _salvar_filas() -> None:
    # Chamado tanto do event loop quanto da thread do player (callback do
    # ffmpeg) - escrever_json troca o arquivo de forma atomica.
    dados = {
        str(guild_id): {"canal_voz": _canal_voz[guild_id], "fila": [asdict(faixa) for faixa in fila]}
        for guild_id, fila in list(_filas_musica.items())
        if fila and guild_id in _canal_voz
    }
    try:
        escrever_json(FILA_PATH, dados)
    except OSError as erro:
        logger.warning(f"Nao consegui salvar a fila de musica: {erro}")


def _carregar_filas() -> dict[int, tuple[int, list[Faixa]]]:
    return {
        int(guild_id): (dados["canal_voz"], [Faixa(**faixa) for faixa in dados["fila"]])
        for guild_id, dados in ler_json(FILA_PATH).items()
    }
_pular_avanco_historico: dict[int, bool] = {}
_ocioso_desde: dict[int, float] = {}
_locks_musica: dict[int, asyncio.Lock] = {}


def _lock_musica(guild_id: int) -> asyncio.Lock:
    # Serializa o trecho "anexar na fila + decidir se toca agora" por
    # servidor - sem isso, dois /musica tocar quase simultaneos podem ambos
    # ver "nao esta tocando" e chamar voice_client.play() duas vezes, o que
    # derruba a segunda chamada com ClientException.
    return _locks_musica.setdefault(guild_id, asyncio.Lock())


def _ytdlp_buscar(busca: str) -> tuple[Faixa, str, str]:
    """Devolve a faixa + url do stream de audio + user-agent exigido por ele."""
    with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
        info = ydl.extract_info(busca, download=False)
        if "entries" in info:
            info = info["entries"][0]
        user_agent = info.get("http_headers", {}).get("User-Agent", "")
        faixa = Faixa(
            titulo=info["title"],
            busca=busca,
            duracao=info.get("duration"),
            pagina=info.get("webpage_url"),
            miniatura=info.get("thumbnail"),
        )
        return faixa, info["url"], user_agent


def _formatar_duracao_musica(segundos) -> str:
    if not segundos:
        return "?"
    segundos = int(segundos)
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{segs:02d}"
    return f"{minutos}:{segs:02d}"


def _tocar_proximo(guild_id: int, voice_client: discord.VoiceClient, tentativas: int = 0) -> None:
    fila = _filas_musica.get(guild_id, [])
    if not fila:
        return

    faixa = fila[0]
    titulo = faixa.titulo

    try:
        # Pelo link do video (quando conhecido), nao pela busca original - uma
        # busca por texto pode cair num video diferente na segunda vez.
        _, url, user_agent = _ytdlp_buscar(faixa.pagina or faixa.busca)
    except Exception as erro:
        logger.error(f"Erro ao extrair musica '{titulo}': {erro}")
        fila.pop(0)
        _salvar_filas()
        _tocar_proximo(guild_id, voice_client)
        return

    ffmpeg_opts = {
        "before_options": f'-user_agent "{user_agent}" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        "options": "-vn",
    }
    fonte = discord.FFmpegPCMAudio(url, **ffmpeg_opts)
    inicio = time.time()
    _inicio_faixa[guild_id] = inicio

    def _apos_tocar(erro):
        duracao = time.time() - inicio
        if duracao < FALHA_REPRODUCAO_SEGUNDOS and tentativas == 0:
            logger.warning(f"'{titulo}' parou em {duracao:.1f}s (provavel link bloqueado), tentando outro link...")
            _tocar_proximo(guild_id, voice_client, tentativas=1)
            return

        if erro:
            logger.error(f"Erro ao tocar '{titulo}': {erro}")

        fila_atual = _filas_musica.get(guild_id, [])
        if _pular_avanco_historico.pop(guild_id, False):
            pass
        elif fila_atual:
            tocada = fila_atual.pop(0)
            historico = _historico_musica.setdefault(guild_id, [])
            historico.append(tocada)
            del historico[:-20]
        _salvar_filas()
        _tocar_proximo(guild_id, voice_client)

    voice_client.play(fonte, after=_apos_tocar)


async def _musica_acao_tocar(interaction: discord.Interaction, busca: str) -> None:
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message("Voce precisa estar em um canal de voz.", ephemeral=True)
        return

    await interaction.response.defer()

    canal_voz = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await canal_voz.connect()
    elif voice_client.channel != canal_voz:
        await voice_client.move_to(canal_voz)
    _canal_voz[interaction.guild.id] = canal_voz.id

    try:
        faixa, _, _ = await asyncio.to_thread(_ytdlp_buscar, busca)
    except Exception as erro:
        # Nivel baixo de proposito - e uma busca sem resultado (situacao normal
        # e ja tratada abaixo), nao uma falha inesperada do sistema.
        logger.info(f"Busca sem resultado para '{busca}': {erro}")
        await interaction.followup.send("Nao consegui encontrar essa musica.")
        return

    duracao_txt = _formatar_duracao_musica(faixa.duracao)

    async with _lock_musica(interaction.guild.id):
        fila = _filas_musica.setdefault(interaction.guild.id, [])
        fila.append(faixa)
        _salvar_filas()

        if not voice_client.is_playing() and not voice_client.is_paused():
            await asyncio.to_thread(_tocar_proximo, interaction.guild.id, voice_client)
            await interaction.followup.send(f"Tocando agora: {faixa.titulo} ({duracao_txt})")
        else:
            await interaction.followup.send(f"Adicionado na fila ({len(fila) - 1}): {faixa.titulo} ({duracao_txt})")


async def _musica_acao_pular(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client
    if voice_client is None or not (voice_client.is_playing() or voice_client.is_paused()):
        await interaction.response.send_message("Nao tem nada tocando agora.", ephemeral=True)
        return

    voice_client.stop()
    await interaction.response.send_message("Musica pulada.")


async def _musica_acao_anterior(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild.id
    historico = _historico_musica.get(guild_id, [])
    if not historico:
        await interaction.response.send_message("Nao ha musica anterior no historico.", ephemeral=True)
        return

    voice_client = interaction.guild.voice_client
    if voice_client is None:
        await interaction.response.send_message("O bot nao esta em nenhum canal de voz.", ephemeral=True)
        return

    anterior = historico.pop()
    fila = _filas_musica.setdefault(guild_id, [])
    fila.insert(0, anterior)
    _salvar_filas()

    await interaction.response.send_message(f"Voltando para: {anterior.titulo} ({_formatar_duracao_musica(anterior.duracao)})")

    if voice_client.is_playing() or voice_client.is_paused():
        _pular_avanco_historico[guild_id] = True
        voice_client.stop()
    else:
        await asyncio.to_thread(_tocar_proximo, guild_id, voice_client)


async def _musica_acao_parar(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        await interaction.response.send_message("O bot nao esta em nenhum canal de voz.", ephemeral=True)
        return

    _filas_musica.pop(interaction.guild.id, None)
    _historico_musica.pop(interaction.guild.id, None)
    _salvar_filas()
    await voice_client.disconnect()
    await interaction.response.send_message("Parado e saindo do canal de voz.")


async def _musica_acao_limpar_fila(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    if len(fila) < 2:
        await interaction.response.send_message(
            "Nao ha musicas na fila para limpar (so a que esta tocando agora, se houver).", ephemeral=True
        )
        return

    _filas_musica[interaction.guild.id] = fila[:1]
    _salvar_filas()
    await interaction.response.send_message("Fila limpa (a musica atual continua tocando).")


def _texto_faixa(faixa: Faixa) -> str:
    titulo = faixa.titulo.replace("[", "(").replace("]", ")")
    nome = f"[{titulo}]({faixa.pagina})" if faixa.pagina else titulo
    return f"{nome} ({_formatar_duracao_musica(faixa.duracao)})"


async def _musica_acao_fila(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    if not fila:
        await interaction.response.send_message("A fila esta vazia.", ephemeral=True)
        return

    linhas = [f"**Tocando agora:** {_texto_faixa(fila[0])}"]
    linhas += [f"{posicao}. {_texto_faixa(faixa)}" for posicao, faixa in enumerate(fila[1:26], start=1)]
    if len(fila) > 26:
        linhas.append(f"-# ... e mais {len(fila) - 26}")
    total = sum(faixa.duracao or 0 for faixa in fila)
    view = layout.view_estatica(
        layout.texto(f"## Fila de musica\n-# {len(fila)} faixa(s), {_formatar_duracao_musica(total)} no total"),
        layout.separador(),
        layout.texto("\n".join(linhas)),
        cor=discord.Color.blurple(),
    )
    await interaction.response.send_message(view=view, ephemeral=True)


class ModalTocarMusica(ModalComRetry, title="Tocar musica"):
    busca = discord.ui.TextInput(label="Link do YouTube ou busca", max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        await _musica_acao_tocar(interaction, str(self.busca))


async def _abrir_modal_tocar(interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(ModalTocarMusica())


class PainelMusica(LayoutViewComRetry):
    """Painel do /musica: o que esta tocando (capa, link e progresso), as
    proximas da fila e os controles."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        fila = _filas_musica.get(guild.id, [])
        voice_client = guild.voice_client
        tocando = fila[0] if fila and voice_client is not None and voice_client.is_playing() else None

        container = discord.ui.Container(accent_color=discord.Color.blurple())
        if tocando is None:
            container.add_item(layout.texto("## Musica\nNada tocando agora. Clique em **Tocar** para comecar."))
        else:
            decorrido = time.time() - _inicio_faixa.get(guild.id, time.time())
            progresso = ""
            if tocando.duracao:
                barra = layout.barra_progresso(decorrido, tocando.duracao, tamanho=16)
                progresso = f"\n`{barra}` {_formatar_duracao_musica(decorrido) if decorrido >= 1 else '0:00'} / {_formatar_duracao_musica(tocando.duracao)}"
            container.add_item(
                layout.secao_com_miniatura(
                    f"## Tocando agora\n{_texto_faixa(tocando)}{progresso}\n-# Canal: {voice_client.channel.mention}",
                    tocando.miniatura,
                )
            )
            proximas = fila[1:4]
            if proximas:
                linhas = [f"{posicao}. {_texto_faixa(faixa)}" for posicao, faixa in enumerate(proximas, start=1)]
                if len(fila) > 4:
                    linhas.append(f"-# ... e mais {len(fila) - 4} na fila")
                container.add_item(layout.separador())
                container.add_item(layout.texto("**A seguir**\n" + "\n".join(linhas)))

        container.add_item(
            layout.linha(
                layout.botao("Tocar", _abrir_modal_tocar, discord.ButtonStyle.success, emoji=emoji("tocar")),
                layout.botao("Anterior", _musica_acao_anterior, emoji=emoji("anterior")),
                layout.botao("Pular", _musica_acao_pular, emoji=emoji("pular")),
                layout.botao("Parar", _musica_acao_parar, discord.ButtonStyle.danger, emoji=emoji("parar")),
            )
        )
        container.add_item(
            layout.linha(
                layout.botao("Fila", _musica_acao_fila, emoji=emoji("fila")),
                layout.botao("Limpar fila", _musica_acao_limpar_fila, discord.ButtonStyle.danger),
                layout.botao("Atualizar", self._atualizar, discord.ButtonStyle.primary, emoji=emoji("atualizar")),
            )
        )
        self.add_item(container)

    async def _atualizar(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=PainelMusica(interaction.guild))


class Musica(commands.Cog):
    musica = app_commands.Group(name="musica", description="Musica no canal de voz", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._fila_restaurada = False
        self._http: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.verificar_ocioso_voz.start()

    async def cog_unload(self) -> None:
        self.verificar_ocioso_voz.cancel()
        if self._http is not None:
            await self._http.close()

    @tasks.loop(seconds=30)
    async def verificar_ocioso_voz(self):
        agora = time.time()
        for guild in self.bot.guilds:
            voice_client = guild.voice_client
            if voice_client is None:
                _ocioso_desde.pop(guild.id, None)
                continue

            if voice_client.is_playing() or voice_client.is_paused():
                _ocioso_desde.pop(guild.id, None)
                continue

            inicio = _ocioso_desde.setdefault(guild.id, agora)
            if agora - inicio >= VOICE_IDLE_TIMEOUT:
                _filas_musica.pop(guild.id, None)
                _ocioso_desde.pop(guild.id, None)
                _inicio_faixa.pop(guild.id, None)
                _salvar_filas()
                await voice_client.disconnect()

    @verificar_ocioso_voz.before_loop
    async def _antes_ocioso(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._fila_restaurada:
            return
        self._fila_restaurada = True
        for guild_id, (canal_id, fila) in (await asyncio.to_thread(_carregar_filas)).items():
            canal = self.bot.get_channel(canal_id)
            # So volta a tocar se ainda tiver gente no canal.
            if canal is None or not fila or not any(not membro.bot for membro in canal.members):
                continue
            try:
                voice_client = canal.guild.voice_client or await canal.connect()
            except (discord.HTTPException, asyncio.TimeoutError, discord.ClientException) as erro:
                logger.warning(f"Nao consegui voltar ao canal de voz {canal_id}: {erro}")
                continue
            _filas_musica[guild_id] = fila
            _canal_voz[guild_id] = canal_id
            if not voice_client.is_playing():
                await asyncio.to_thread(_tocar_proximo, guild_id, voice_client)
            logger.info(f"Fila de musica restaurada em {guild_id} ({len(fila)} faixa(s))")
        await asyncio.to_thread(_salvar_filas)

    @musica.command(name="tocar", description="Toca uma musica (ou poe na fila) - link do YouTube ou busca")
    @app_commands.describe(busca="Link do YouTube ou o nome da musica")
    async def tocar(self, interaction: discord.Interaction, busca: app_commands.Range[str, 1, 200]):
        await _musica_acao_tocar(interaction, busca)

    @tocar.autocomplete("busca")
    async def _sugestoes_youtube(self, interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[str]]:
        # Sugestoes de busca do proprio YouTube enquanto a pessoa digita. O
        # Discord so espera 3s pelo autocomplete, entao desiste rapido.
        atual = atual.strip()
        if len(atual) < 2 or atual.startswith(("http://", "https://")):
            return []
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.5))
        try:
            async with self._http.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "ds": "yt", "q": atual},
            ) as resposta:
                dados = await resposta.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return []
        sugestoes = [atual, *(s for s in dados[1] if s.lower() != atual.lower())] if len(dados) > 1 else [atual]
        return [app_commands.Choice(name=s[:100], value=s[:100]) for s in sugestoes[:25]]

    @musica.command(name="pular", description="Pula para a proxima musica da fila")
    async def pular(self, interaction: discord.Interaction):
        await _musica_acao_pular(interaction)

    @musica.command(name="anterior", description="Volta para a musica anterior")
    async def anterior(self, interaction: discord.Interaction):
        await _musica_acao_anterior(interaction)

    @musica.command(name="parar", description="Para a musica, limpa a fila e sai do canal de voz")
    async def parar(self, interaction: discord.Interaction):
        await _musica_acao_parar(interaction)

    @musica.command(name="fila", description="Mostra a fila de musicas")
    async def fila(self, interaction: discord.Interaction):
        await _musica_acao_fila(interaction)

    @musica.command(name="limpar", description="Limpa a fila (a musica atual continua tocando)")
    async def limpar(self, interaction: discord.Interaction):
        await _musica_acao_limpar_fila(interaction)

    @musica.command(name="painel", description="Abre o painel com o que esta tocando e os botoes de controle")
    async def painel(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=PainelMusica(interaction.guild), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Musica(bot))
