import asyncio
import json
import os
import platform
import random
import re
import subprocess
import time

import aiohttp
import discord
import psutil
import yt_dlp
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY")
MC_BOOT_TIMEOUT = 300
MC_BOOT_CHECK_INTERVAL = 10
MC_SHUTDOWN_TIMEOUT = 60
MC_CHAT_CHANNEL_ID = int(os.getenv("MC_CHAT_CHANNEL_ID", "0") or 0)
# IP/host dinamico para conexao (edite no .env quando o IP mudar).
MC_HOST = os.getenv("MC_HOST", "")

# Texto personalizavel do status do bot: usado no Custom Status (balao ao lado
# do avatar) e tambem no "Jogando X" quando nenhum servidor esta ligado.
STATUS_SEM_SERVIDOR = os.getenv("STATUS_SEM_SERVIDOR", "Tempo acordado")

# Servidores de Minecraft controlaveis pelo bot. Para adicionar um novo,
# basta incluir mais uma entrada aqui com diretorio e comando de inicializacao.
MC_SERVERS = {
    "ftbskies2": {
        "nome": "FTB Skies 2",
        "dir": "/Users/marcos/server_minecraft/ftbskies2",
        "comando": ["./run.sh"],
    },
    "vanilla": {
        "nome": "Vanilla 26.2",
        "dir": "/Users/marcos/server_minecraft/vanilla",
        "comando": ["java", "-Xmx3G", "-jar", "run.jar", "nogui"],
    },
}

SERVIDOR_CHOICES = [app_commands.Choice(name=cfg["nome"], value=chave) for chave, cfg in MC_SERVERS.items()]

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "status", "ajuda"],
    "Diversao": ["gif", "socao", "abraco"],
    "Musica": ["tocar", "pular", "parar", "volume"],
    "Minecraft": ["mc_ligar", "mc_desligar", "mc_reiniciar"],
    "Administracao (dono)": ["desligar", "reiniciar"],
}

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

_filas_musica: dict[int, list[tuple[str, str]]] = {}
_volumes_musica: dict[int, float] = {}
_ocioso_desde: dict[int, float] = {}

MC_CHAT_LINE_RE = re.compile(r"\]: (.+)$")
MC_CHAT_MSG_RE = re.compile(r"^<([^>]+)> (.+)$")
MC_CHAT_JOIN_RE = re.compile(r"^(\S+) joined the game$")
MC_CHAT_LEAVE_RE = re.compile(r"^(\S+) left the game$")

_mc_log_offsets: dict[str, int] = {}
_mc_log_inodes: dict[str, int] = {}

BOT_INICIO = time.time()

intents = discord.Intents.default()

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

owner_id = None


def _formatar_duracao(segundos: int) -> str:
    horas, segundos_restantes = divmod(segundos, 3600)
    minutos, _ = divmod(segundos_restantes, 60)
    if horas:
        return f"{horas}h{minutos}m"
    return f"{minutos}m"


def _bot_tempo_ligado() -> str:
    return _formatar_duracao(int(time.time() - BOT_INICIO))


async def _change_presence_multipla(atividades: list) -> None:
    payload = {
        "op": client.ws.PRESENCE,
        "d": {
            "activities": [a.to_dict() for a in atividades],
            "afk": False,
            "since": 0,
            "status": "online",
        },
    }
    await client.ws.send(json.dumps(payload))


@tasks.loop(seconds=60)
async def atualizar_presence():
    atividade_jogo = discord.Game(name=f"{STATUS_SEM_SERVIDOR} {_bot_tempo_ligado()}")
    atividade_custom = discord.CustomActivity(name=atividade_jogo.name)
    await _change_presence_multipla([atividade_jogo, atividade_custom])


@tasks.loop(seconds=2)
async def monitorar_chat_minecraft():
    if not MC_CHAT_CHANNEL_ID:
        return

    canal = client.get_channel(MC_CHAT_CHANNEL_ID)
    if canal is None:
        return

    prefixar = len(MC_SERVERS) > 1
    for chave, config in MC_SERVERS.items():
        linhas = await asyncio.to_thread(_mc_ler_novas_linhas, chave, config)
        for linha in linhas:
            evento = _mc_formatar_evento_chat(linha)
            if evento:
                prefixo = f"[{config['nome']}] " if prefixar else ""
                await canal.send(f"{prefixo}{evento}")


@tasks.loop(seconds=30)
async def verificar_ocioso_voz():
    agora = time.time()
    for guild in client.guilds:
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


@client.event
async def on_ready():
    global owner_id
    if not atualizar_presence.is_running():
        atualizar_presence.start()
    if not monitorar_chat_minecraft.is_running():
        monitorar_chat_minecraft.start()
    if not verificar_ocioso_voz.is_running():
        verificar_ocioso_voz.start()

    app_info = await client.application_info()
    owner_id = app_info.owner.id
    await tree.sync()
    print(f"Bot conectado como {client.user}")


@tree.command(name="ping", description="Testa se o bot esta online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


@tree.command(name="ajuda", description="Mostra todos os comandos do bot, por categoria")
async def ajuda(interaction: discord.Interaction):
    comandos_por_nome = {c.name: c for c in tree.get_commands() if isinstance(c, app_commands.Command)}

    embed = discord.Embed(title="Comandos disponiveis", color=discord.Color.blue())
    for categoria, nomes in COMANDOS_CATEGORIAS.items():
        linhas = []
        for nome in nomes:
            comando = comandos_por_nome.get(nome)
            if comando:
                linhas.append(f"`/{nome}` - {comando.description}")
        if linhas:
            embed.add_field(name=categoria, value="\n".join(linhas), inline=False)

    await interaction.response.send_message(embed=embed)


@tree.command(name="status", description="Mostra uso de CPU, RAM e disco da maquina")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()

    cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
    ram = psutil.virtual_memory()
    if platform.system() == "Windows":
        disk_path = os.getenv("SystemDrive", "C:") + "\\"
    elif platform.system() == "Darwin" and os.path.exists("/System/Volumes/Data"):
        disk_path = "/System/Volumes/Data"
    else:
        disk_path = "/"
    disk = psutil.disk_usage(disk_path)

    embed = discord.Embed(title="Monitoramento do servidor", color=discord.Color.blue())
    embed.add_field(name="CPU", value=f"{cpu}%", inline=False)
    embed.add_field(
        name="RAM",
        value=f"{ram.percent}% ({ram.used // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        inline=False,
    )
    embed.add_field(
        name="Disco",
        value=f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
        inline=False,
    )

    for config in MC_SERVERS.values():
        tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
        if tempo is None:
            valor = "Desligado"
        elif await asyncio.to_thread(_mc_servidor_pronto, config):
            valor = f"Ligado ha {tempo}"
        else:
            valor = f"Ligando... ({tempo})"
        embed.add_field(name=config["nome"], value=valor, inline=False)

    await interaction.followup.send(embed=embed)


async def _buscar_gif(busca: str) -> str | None:
    params = {"api_key": GIPHY_API_KEY, "q": busca, "limit": 25, "rating": "pg-13"}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.giphy.com/v1/gifs/search", params=params) as resp:
            data = await resp.json()

    resultados = data.get("data")
    if not resultados:
        return None
    return random.choice(resultados)["images"]["original"]["url"]


@tree.command(name="gif", description="Busca um gif pela palavra-chave")
@app_commands.describe(busca="Palavra-chave para buscar o gif")
async def gif(interaction: discord.Interaction, busca: str):
    await interaction.response.defer()

    gif_url = await _buscar_gif(busca)
    if gif_url is None:
        await interaction.followup.send(f'Nenhum gif encontrado para "{busca}".')
        return

    await interaction.followup.send(gif_url)


@tree.command(name="socao", description="Da um socao (com gif de anime) em alguem")
@app_commands.describe(pessoa="Quem vai levar o socao")
async def socao(interaction: discord.Interaction, pessoa: discord.Member):
    await interaction.response.defer()

    gif_url = await _buscar_gif("anime punch")
    if gif_url is None:
        await interaction.followup.send("Nao encontrei nenhum gif de socao agora.")
        return

    embed = discord.Embed()
    embed.set_image(url=gif_url)
    await interaction.followup.send(
        content=f"{interaction.user.mention} deu um socao em {pessoa.mention}", embed=embed
    )


@tree.command(name="abraco", description="Da um abraco (com gif de anime) em alguem")
@app_commands.describe(pessoa="Quem vai levar o abraco")
async def abraco(interaction: discord.Interaction, pessoa: discord.Member):
    await interaction.response.defer()

    gif_url = await _buscar_gif("anime hug")
    if gif_url is None:
        await interaction.followup.send("Nao encontrei nenhum gif de abraco agora.")
        return

    embed = discord.Embed()
    embed.set_image(url=gif_url)
    await interaction.followup.send(
        content=f"{interaction.user.mention} deu um abraco em {pessoa.mention}", embed=embed
    )


def _ytdlp_buscar(busca: str) -> tuple[str, str, str]:
    with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
        info = ydl.extract_info(busca, download=False)
        if "entries" in info:
            info = info["entries"][0]
        user_agent = info.get("http_headers", {}).get("User-Agent", "")
        return info["title"], info["url"], user_agent


def _tocar_proximo(guild_id: int, voice_client: discord.VoiceClient, tentativas: int = 0) -> None:
    fila = _filas_musica.get(guild_id, [])
    if not fila:
        return

    titulo, busca = fila[0]

    try:
        _, url, user_agent = _ytdlp_buscar(busca)
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
        if fila_atual:
            fila_atual.pop(0)
        _tocar_proximo(guild_id, voice_client)

    voice_client.play(fonte, after=_apos_tocar)


@tree.command(name="tocar", description="Toca uma musica do YouTube (link ou busca)")
@app_commands.describe(busca="Link do YouTube ou termo de busca")
async def tocar(interaction: discord.Interaction, busca: str):
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
        titulo, _, _ = await asyncio.to_thread(_ytdlp_buscar, busca)
    except Exception as erro:
        print(f"Erro ao buscar musica '{busca}': {erro}")
        await interaction.followup.send("Nao consegui encontrar essa musica.")
        return

    fila = _filas_musica.setdefault(interaction.guild.id, [])
    fila.append((titulo, busca))

    if not voice_client.is_playing() and not voice_client.is_paused():
        await asyncio.to_thread(_tocar_proximo, interaction.guild.id, voice_client)
        await interaction.followup.send(f"Tocando agora: {titulo}")
    else:
        await interaction.followup.send(f"Adicionado na fila: {titulo}")


@tree.command(name="pular", description="Pula a musica atual")
async def pular(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client is None or not (voice_client.is_playing() or voice_client.is_paused()):
        await interaction.response.send_message("Nao tem nada tocando agora.", ephemeral=True)
        return

    voice_client.stop()
    await interaction.response.send_message("Musica pulada.")


@tree.command(name="parar", description="Para a musica e sai do canal de voz")
async def parar(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        await interaction.response.send_message("O bot nao esta em nenhum canal de voz.", ephemeral=True)
        return

    _filas_musica.pop(interaction.guild.id, None)
    await voice_client.disconnect()
    await interaction.response.send_message("Parado e saindo do canal de voz.")


@tree.command(name="volume", description="Ajusta o volume da musica (0 a 200%)")
@app_commands.describe(porcentagem="Volume em porcentagem (0 a 200)")
async def volume(interaction: discord.Interaction, porcentagem: app_commands.Range[int, 0, 200]):
    volume_normalizado = porcentagem / 100
    _volumes_musica[interaction.guild.id] = volume_normalizado

    voice_client = interaction.guild.voice_client
    if voice_client is not None and isinstance(voice_client.source, discord.PCMVolumeTransformer):
        voice_client.source.volume = volume_normalizado

    await interaction.response.send_message(f"Volume ajustado para {porcentagem}%.")


def _mc_porta(config) -> str | None:
    try:
        with open(os.path.join(config["dir"], "server.properties"), "r", encoding="utf-8", errors="ignore") as arquivo:
            for linha in arquivo:
                if linha.startswith("server-port="):
                    return linha.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return None


def _mc_processo(config):
    for proc in psutil.process_iter(["cwd", "name"]):
        try:
            if proc.info["cwd"] == config["dir"] and "java" in proc.info["name"].lower():
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _mc_servidor_rodando(config) -> bool:
    return _mc_processo(config) is not None


def _mc_servidor_pronto(config) -> bool:
    log_path = os.path.join(config["dir"], "logs", "latest.log")
    resultado = subprocess.run(["grep", "-m", "1", "Done (", log_path], capture_output=True, text=True)
    return resultado.returncode == 0


def _mc_tempo_ligado(config) -> str | None:
    processo = _mc_processo(config)
    if processo is None:
        return None
    return _formatar_duracao(int(time.time() - processo.create_time()))


def _mc_ler_novas_linhas(chave: str, config) -> list[str]:
    log_path = os.path.join(config["dir"], "logs", "latest.log")
    try:
        stat = os.stat(log_path)
    except FileNotFoundError:
        return []

    if _mc_log_inodes.get(chave) != stat.st_ino:
        _mc_log_inodes[chave] = stat.st_ino
        _mc_log_offsets[chave] = stat.st_size
        return []

    offset = _mc_log_offsets.get(chave, 0)
    if stat.st_size < offset:
        offset = 0

    with open(log_path, "r", encoding="utf-8", errors="ignore") as arquivo:
        arquivo.seek(offset)
        linhas = arquivo.readlines()
        _mc_log_offsets[chave] = arquivo.tell()
    return linhas


def _mc_formatar_evento_chat(linha: str) -> str | None:
    correspondencia = MC_CHAT_LINE_RE.search(linha)
    if not correspondencia:
        return None
    conteudo = correspondencia.group(1)

    mensagem = MC_CHAT_MSG_RE.match(conteudo)
    if mensagem:
        return f"**{mensagem.group(1)}**: {mensagem.group(2)}"

    entrou = MC_CHAT_JOIN_RE.match(conteudo)
    if entrou:
        return f"*{entrou.group(1)} entrou no servidor*"

    saiu = MC_CHAT_LEAVE_RE.match(conteudo)
    if saiu:
        return f"*{saiu.group(1)} saiu do servidor*"

    return None


def _mc_info_conexao(config) -> str:
    porta = _mc_porta(config)
    if MC_HOST and porta:
        return f"\nConecte em: {MC_HOST}:{porta}"
    return ""


@tree.command(name="mc_ligar", description="Liga um servidor de Minecraft")
@app_commands.describe(servidor="Qual servidor ligar")
@app_commands.choices(servidor=SERVIDOR_CHOICES)
async def mc_ligar(interaction: discord.Interaction, servidor: app_commands.Choice[str]):
    config = MC_SERVERS[servidor.value]
    await interaction.response.defer()

    if await asyncio.to_thread(_mc_servidor_rodando, config):
        if await asyncio.to_thread(_mc_servidor_pronto, config):
            info = await asyncio.to_thread(_mc_info_conexao, config)
            await interaction.followup.send(f"{config['nome']} ja esta ligado e pronto para conexoes.{info}")
        else:
            await interaction.followup.send(f"{config['nome']} ja esta ligando, aguarde.")
        return

    subprocess.Popen(
        config["comando"],
        cwd=config["dir"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    await interaction.followup.send(f"Ligando {config['nome']}, aguarde...")

    for _ in range(MC_BOOT_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if await asyncio.to_thread(_mc_servidor_pronto, config):
            info = await asyncio.to_thread(_mc_info_conexao, config)
            await interaction.edit_original_response(
                content=f"{config['nome']} ligado e pronto para conexoes!{info}"
            )
            return

    await interaction.edit_original_response(
        content=f"{config['nome']} esta demorando mais que o esperado para subir. Use /status para verificar."
    )


@tree.command(name="mc_desligar", description="Desliga um servidor de Minecraft")
@app_commands.describe(servidor="Qual servidor desligar")
@app_commands.choices(servidor=SERVIDOR_CHOICES)
async def mc_desligar(interaction: discord.Interaction, servidor: app_commands.Choice[str]):
    config = MC_SERVERS[servidor.value]
    await interaction.response.defer()

    processo = await asyncio.to_thread(_mc_processo, config)
    if processo is None:
        await interaction.followup.send(f"{config['nome']} nao esta rodando.")
        return

    tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
    processo.terminate()

    mensagem = f"Desligando {config['nome']} (salvando o mundo)..."
    if tempo:
        mensagem += f"\nFicou ligado por {tempo}."
    await interaction.followup.send(mensagem)


@tree.command(name="mc_reiniciar", description="Reinicia um servidor de Minecraft")
@app_commands.describe(servidor="Qual servidor reiniciar")
@app_commands.choices(servidor=SERVIDOR_CHOICES)
async def mc_reiniciar(interaction: discord.Interaction, servidor: app_commands.Choice[str]):
    config = MC_SERVERS[servidor.value]
    await interaction.response.defer()

    processo = await asyncio.to_thread(_mc_processo, config)
    if processo is None:
        await interaction.followup.send(f"{config['nome']} nao esta rodando. Use /mc_ligar para liga-lo.")
        return

    await interaction.followup.send(f"Reiniciando {config['nome']} (salvando o mundo)...")
    processo.terminate()

    for _ in range(MC_SHUTDOWN_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if not await asyncio.to_thread(_mc_servidor_rodando, config):
            break
    else:
        await interaction.edit_original_response(
            content=f"{config['nome']} esta demorando para desligar. Tente novamente em instantes."
        )
        return

    subprocess.Popen(
        config["comando"],
        cwd=config["dir"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    await interaction.edit_original_response(content=f"{config['nome']} desligou, ligando de novo, aguarde...")

    for _ in range(MC_BOOT_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if await asyncio.to_thread(_mc_servidor_pronto, config):
            info = await asyncio.to_thread(_mc_info_conexao, config)
            await interaction.edit_original_response(
                content=f"{config['nome']} reiniciado e pronto para conexoes!{info}"
            )
            return

    await interaction.edit_original_response(
        content=f"{config['nome']} esta demorando mais que o esperado para subir. Use /status para verificar."
    )


@tree.command(name="desligar", description="Desliga o bot (apenas o dono)")
async def desligar(interaction: discord.Interaction):
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

    await interaction.response.send_message("Desligando o bot...")
    await client.close()


@tree.command(name="reiniciar", description="Reinicia o bot (apenas o dono)")
async def reiniciar(interaction: discord.Interaction):
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

    await interaction.response.send_message("Reiniciando o bot...")
    await client.close()


if __name__ == "__main__":
    client.run(TOKEN)
