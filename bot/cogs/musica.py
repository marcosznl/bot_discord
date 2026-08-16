import asyncio
import random
import time

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

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

_filas_musica: dict[int, list[tuple[str, str, int | None]]] = {}
_volumes_musica: dict[int, float] = {}
_repetir_musica: dict[int, str] = {}  # guild_id -> "atual", "fila" ou ausente (desligado)
_historico_musica: dict[int, list[tuple[str, str, int | None]]] = {}
_pular_avanco_historico: dict[int, bool] = {}
_ocioso_desde: dict[int, float] = {}


def _ytdlp_buscar(busca: str) -> tuple[str, str, str, int | None]:
    with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
        info = ydl.extract_info(busca, download=False)
        if "entries" in info:
            info = info["entries"][0]
        user_agent = info.get("http_headers", {}).get("User-Agent", "")
        return info["title"], info["url"], user_agent, info.get("duration")


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

    titulo, busca, _ = fila[0]

    try:
        _, url, user_agent, _ = _ytdlp_buscar(busca)
    except Exception as erro:
        print(f"Erro ao extrair musica '{titulo}': {erro}")
        fila.pop(0)
        _tocar_proximo(guild_id, voice_client)
        return

    volume = _volumes_musica.get(guild_id, 1.0)
    ffmpeg_opts = {
        "before_options": f'-user_agent "{user_agent}" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        "options": "-vn",
    }
    fonte = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **ffmpeg_opts), volume=volume)
    inicio = time.time()

    def _apos_tocar(erro):
        duracao = time.time() - inicio
        if duracao < FALHA_REPRODUCAO_SEGUNDOS and tentativas == 0:
            print(f"'{titulo}' parou em {duracao:.1f}s (provavel link bloqueado), tentando outro link...")
            _tocar_proximo(guild_id, voice_client, tentativas=1)
            return

        if erro:
            print(f"Erro ao tocar '{titulo}': {erro}")

        fila_atual = _filas_musica.get(guild_id, [])
        modo_repetir = _repetir_musica.get(guild_id)
        if _pular_avanco_historico.pop(guild_id, False):
            pass
        elif modo_repetir == "atual":
            pass
        elif fila_atual:
            tocada = fila_atual.pop(0)
            historico = _historico_musica.setdefault(guild_id, [])
            historico.append(tocada)
            del historico[:-20]
            if modo_repetir == "fila":
                fila_atual.append(tocada)
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

    try:
        titulo, _, _, duracao = await asyncio.to_thread(_ytdlp_buscar, busca)
    except Exception as erro:
        print(f"Erro ao buscar musica '{busca}': {erro}")
        await interaction.followup.send("Nao consegui encontrar essa musica.")
        return

    fila = _filas_musica.setdefault(interaction.guild.id, [])
    fila.append((titulo, busca, duracao))
    duracao_txt = _formatar_duracao_musica(duracao)

    if not voice_client.is_playing() and not voice_client.is_paused():
        await asyncio.to_thread(_tocar_proximo, interaction.guild.id, voice_client)
        await interaction.followup.send(f"Tocando agora: {titulo} ({duracao_txt})")
    else:
        await interaction.followup.send(f"Adicionado na fila: {titulo} ({duracao_txt})")


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

    await interaction.response.send_message(f"Voltando para: {anterior[0]} ({_formatar_duracao_musica(anterior[2])})")

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
    await voice_client.disconnect()
    await interaction.response.send_message("Parado e saindo do canal de voz.")


async def _musica_acao_pausar(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client
    if voice_client is None or not voice_client.is_playing():
        await interaction.response.send_message("Nao tem nada tocando agora.", ephemeral=True)
        return

    voice_client.pause()
    await interaction.response.send_message("Musica pausada.")


async def _musica_acao_continuar(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client
    if voice_client is None or not voice_client.is_paused():
        await interaction.response.send_message("Nao tem nada pausado agora.", ephemeral=True)
        return

    voice_client.resume()
    await interaction.response.send_message("Musica retomada.")


async def _musica_acao_embaralhar(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    pendentes = fila[1:]
    if len(pendentes) < 2:
        await interaction.response.send_message("Nao ha musicas suficientes na fila para embaralhar.", ephemeral=True)
        return

    random.shuffle(pendentes)
    fila[1:] = pendentes
    await interaction.response.send_message("Fila embaralhada.")


async def _musica_acao_repetir(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild.id
    modo_atual = _repetir_musica.get(guild_id)

    if modo_atual is None:
        _repetir_musica[guild_id] = "atual"
        texto = "Repeticao da musica atual ativada."
    elif modo_atual == "atual":
        _repetir_musica[guild_id] = "fila"
        texto = "Repeticao trocada para a fila inteira (quando acabar, comeca de novo do inicio)."
    else:
        _repetir_musica.pop(guild_id, None)
        texto = "Repeticao desativada."

    await interaction.response.send_message(texto)


async def _musica_acao_limpar_fila(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    if len(fila) < 2:
        await interaction.response.send_message(
            "Nao ha musicas na fila para limpar (so a que esta tocando agora, se houver).", ephemeral=True
        )
        return

    _filas_musica[interaction.guild.id] = fila[:1]
    await interaction.response.send_message("Fila limpa (a musica atual continua tocando).")


async def _musica_acao_volume(interaction: discord.Interaction, porcentagem: int) -> None:
    volume_normalizado = porcentagem / 100
    _volumes_musica[interaction.guild.id] = volume_normalizado

    voice_client = interaction.guild.voice_client
    if voice_client is not None and isinstance(voice_client.source, discord.PCMVolumeTransformer):
        voice_client.source.volume = volume_normalizado

    await interaction.response.send_message(f"Volume ajustado para {porcentagem}%.")


async def _musica_acao_fila(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    if not fila:
        await interaction.response.send_message("A fila esta vazia.", ephemeral=True)
        return

    linhas = []
    for posicao, (titulo, _, duracao) in enumerate(fila):
        duracao_txt = _formatar_duracao_musica(duracao)
        if posicao == 0:
            linhas.append(f"**Tocando agora:** {titulo} ({duracao_txt})")
        else:
            linhas.append(f"{posicao}. {titulo} ({duracao_txt})")

    embed = discord.Embed(title="Fila de musica", description="\n".join(linhas), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)


class SelecionarRemoverFila(discord.ui.View):
    def __init__(self, guild_id: int, fila: list[tuple[str, str, int | None]]):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        opcoes = [
            discord.SelectOption(label=titulo[:100], value=str(indice))
            for indice, (titulo, _, _) in enumerate(fila)
            if indice > 0
        ][:25]
        select = discord.ui.Select(placeholder="Escolha a musica para remover", options=opcoes)
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        indice = int(interaction.data["values"][0])
        fila = _filas_musica.get(self.guild_id, [])
        if indice < len(fila):
            titulo, _ = fila.pop(indice)
            await interaction.response.edit_message(content=f"Removido da fila: {titulo}", view=None)
        else:
            await interaction.response.edit_message(content="Essa musica ja nao esta mais na fila.", view=None)


async def _musica_acao_remover(interaction: discord.Interaction) -> None:
    fila = _filas_musica.get(interaction.guild.id, [])
    if len(fila) < 2:
        await interaction.response.send_message(
            "Nao ha musicas na fila para remover (so a que esta tocando agora).", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "Qual musica remover da fila?", view=SelecionarRemoverFila(interaction.guild.id, fila), ephemeral=True
    )


class ModalTocarMusica(discord.ui.Modal, title="Tocar musica"):
    busca = discord.ui.TextInput(label="Link do YouTube ou busca", max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        await _musica_acao_tocar(interaction, str(self.busca))


class ModalVolumeMusica(discord.ui.Modal, title="Ajustar volume"):
    porcentagem = discord.ui.TextInput(label="Volume em % (0 a 200)", placeholder="100", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.porcentagem).strip()
        if not texto.isdigit() or not (0 <= int(texto) <= 200):
            await interaction.response.send_message("Informe um numero entre 0 e 200.", ephemeral=True)
            return
        await _musica_acao_volume(interaction, int(texto))


class BotoesMusica(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Tocar", style=discord.ButtonStyle.success)
    async def tocar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalTocarMusica())

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_anterior(interaction)

    @discord.ui.button(label="Pular", style=discord.ButtonStyle.secondary)
    async def pular(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_pular(interaction)

    @discord.ui.button(label="Parar", style=discord.ButtonStyle.danger)
    async def parar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_parar(interaction)

    @discord.ui.button(label="Volume", style=discord.ButtonStyle.primary)
    async def volume(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalVolumeMusica())

    @discord.ui.button(label="Fila", style=discord.ButtonStyle.secondary)
    async def fila(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_fila(interaction)

    @discord.ui.button(label="Pausar", style=discord.ButtonStyle.secondary)
    async def pausar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_pausar(interaction)

    @discord.ui.button(label="Continuar", style=discord.ButtonStyle.success)
    async def continuar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_continuar(interaction)

    @discord.ui.button(label="Embaralhar", style=discord.ButtonStyle.primary)
    async def embaralhar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_embaralhar(interaction)

    @discord.ui.button(label="Remover da fila", style=discord.ButtonStyle.danger)
    async def remover(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_remover(interaction)

    @discord.ui.button(label="Repetir", style=discord.ButtonStyle.primary)
    async def repetir(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_repetir(interaction)

    @discord.ui.button(label="Limpar fila", style=discord.ButtonStyle.danger)
    async def limpar_fila(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_limpar_fila(interaction)


class Musica(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.verificar_ocioso_voz.cancel()

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
                await voice_client.disconnect()

    @app_commands.command(name="musica", description="Controla a reproducao de musica")
    async def musica(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "O que voce quer fazer com a musica?", view=BotoesMusica()
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Musica(bot))
