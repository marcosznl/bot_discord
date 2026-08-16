import asyncio
import json
import os
import platform
import random
import re
import sqlite3
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

# Endereco da API local do LM Studio (compativel com o formato da OpenAI).
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1/chat/completions")
LLM_PROMPT_SISTEMA = "Voce e um assistente respondendo em um servidor do Discord. Seja conciso."
LMS_CLI = os.path.expanduser("~/.lmstudio/bin/lms")
LLM_MODELO = os.getenv("LLM_MODELO", "google/gemma-4-e4b")
# Quantas mensagens (usuario + assistente) ficam guardadas por conversa.
LLM_HISTORICO_MAX_MENSAGENS = 20

# Descricao curta do ponto forte de cada modelo, usada pelo /ajuda_llm.
# Modelos baixados sem entrada aqui aparecem com uma descricao generica.
LLM_DESCRICOES = {
    "qwen/qwen3.5-9b": "Generalista forte, bom em varios idiomas e uso de ferramentas, contexto bem grande.",
    "google/gemma-4-12b": "Modelo maior do Google, bom para tarefas gerais e consegue analisar imagens.",
    "deepseek/deepseek-r1-0528-qwen3-8b": "Focado em raciocinio - melhor para logica, matematica e problemas passo a passo.",
    "microsoft/phi-4-mini-reasoning": "Pequeno e leve, tambem focado em raciocinio - ideal para economizar RAM.",
    "google/gemma-4-e4b": "Generalista leve do Google, tambem analisa imagens - bom modelo padrao do dia a dia.",
}

_llm_historico: dict[tuple[int | None, int, int], list[dict]] = {}

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
    "Geral": ["ping", "status", "velocidade_server", "ajuda"],
    "Moderacao": ["limpar"],
    "Niveis": ["rank", "leaderboard", "ajuda_xp"],
    "Diversao": ["gif", "socao", "abraco", "charme"],
    "Musica": ["tocar", "pular", "parar", "volume"],
    "Minecraft": ["mc_ligar", "mc_desligar", "mc_reiniciar"],
    "LLM": ["llm_ligar", "llm_desligar", "llm_trocar", "ajuda_llm", "llm_esquecer"],
    "Administracao (dono)": ["desligar", "reiniciar"],
}

XP_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xp.db")
XP_COOLDOWN_SEGUNDOS = 20
XP_MIN_POR_MENSAGEM = 15
XP_MAX_POR_MENSAGEM = 25

# Cargos concedidos automaticamente ao atingir cada nivel. Preencha com o ID
# do cargo: ative o Modo Desenvolvedor no Discord (Configuracoes > Avancado),
# clique com botao direito no cargo desejado e escolha "Copiar ID".
# Exemplo: {5: 123456789012345678, 10: 123456789012345679}
CARGOS_POR_NIVEL: dict[int, int] = {}

_xp_cooldowns: dict[tuple[int, int], float] = {}

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


def _xp_conectar_bd() -> sqlite3.Connection:
    conexao = sqlite3.connect(XP_DB_PATH)
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS xp_usuarios ("
        "guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (guild_id, user_id))"
    )
    return conexao


def _xp_total_ate_nivel(nivel: int) -> int:
    total = 0
    for n in range(nivel):
        total += 5 * (n**2) + 50 * n + 100
    return total


def _xp_calcular_nivel(xp_total: int) -> int:
    nivel = 0
    while xp_total >= _xp_total_ate_nivel(nivel + 1):
        nivel += 1
    return nivel


def _xp_progresso(xp_total: int) -> tuple[int, int, int]:
    nivel = _xp_calcular_nivel(xp_total)
    base_atual = _xp_total_ate_nivel(nivel)
    base_proximo = _xp_total_ate_nivel(nivel + 1)
    return nivel, xp_total - base_atual, base_proximo - base_atual


def _xp_obter(guild_id: int, user_id: int) -> int:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute(
            "SELECT xp FROM xp_usuarios WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
    return linha[0] if linha else 0


def _xp_adicionar(guild_id: int, user_id: int, quantidade: int) -> tuple[int, int]:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute(
            "SELECT xp FROM xp_usuarios WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        xp_antes = linha[0] if linha else 0
        xp_depois = xp_antes + quantidade
        conexao.execute(
            "INSERT INTO xp_usuarios (guild_id, user_id, xp) VALUES (?, ?, ?) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = excluded.xp",
            (guild_id, user_id, xp_depois),
        )
        conexao.commit()
    return xp_antes, xp_depois


def _xp_leaderboard(guild_id: int, limite: int = 10) -> list[tuple[int, int]]:
    with _xp_conectar_bd() as conexao:
        linhas = conexao.execute(
            "SELECT user_id, xp FROM xp_usuarios WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (guild_id, limite),
        ).fetchall()
    return linhas


def _llm_servidor_rodando() -> bool:
    resultado = subprocess.run([LMS_CLI, "server", "status"], capture_output=True, text=True)
    saida = (resultado.stdout + resultado.stderr).lower()
    return "is running" in saida


def _llm_processo():
    for proc in psutil.process_iter(["cmdline", "name"]):
        try:
            cmdline = proc.info["cmdline"] or []
            if any("llmworker.js" in parte for parte in cmdline):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _llm_ram_mb() -> int | None:
    processo = _llm_processo()
    if processo is None:
        return None
    return processo.memory_info().rss // (1024**2)


def _llm_modelos_disponiveis() -> list[str]:
    resultado = subprocess.run([LMS_CLI, "ls", "--llm", "--json"], capture_output=True, text=True)
    try:
        modelos = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return []
    return [m["modelKey"] for m in modelos]


async def _llm_autocomplete_modelo(
    interaction: discord.Interaction, atual: str
) -> list[app_commands.Choice[str]]:
    modelos = await asyncio.to_thread(_llm_modelos_disponiveis)
    return [
        app_commands.Choice(name=modelo, value=modelo)
        for modelo in modelos
        if atual.lower() in modelo.lower()
    ][:25]


def _llm_chave_conversa(message: discord.Message) -> tuple[int | None, int, int]:
    guild_id = message.guild.id if message.guild else None
    return (guild_id, message.channel.id, message.author.id)


async def _consultar_llm_local(historico: list[dict]) -> str:
    payload = {
        "messages": [{"role": "system", "content": LLM_PROMPT_SISTEMA}, *historico],
        "temperature": 0.7,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(LM_STUDIO_URL, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            dados = await resp.json()
    return dados["choices"][0]["message"]["content"]


async def _responder_llm(message: discord.Message) -> None:
    pergunta = message.content
    if client.user in message.mentions:
        pergunta = re.sub(rf"<@!?{client.user.id}>", "", pergunta).strip()

    if not pergunta:
        return

    historico = _llm_historico.setdefault(_llm_chave_conversa(message), [])
    historico.append({"role": "user", "content": pergunta})

    async with message.channel.typing():
        try:
            resposta = await _consultar_llm_local(historico)
        except Exception as erro:
            print(f"Erro ao consultar LLM local: {erro}")
            await message.channel.send("Nao consegui falar com a LLM local agora. Ela esta rodando?")
            historico.pop()
            return

    historico.append({"role": "assistant", "content": resposta})
    del historico[:-LLM_HISTORICO_MAX_MENSAGENS]

    for inicio in range(0, len(resposta), 1900):
        await message.channel.send(resposta[inicio : inicio + 1900])


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.guild is not None:
        chave = (message.guild.id, message.author.id)
        agora = time.time()
        if agora - _xp_cooldowns.get(chave, 0) >= XP_COOLDOWN_SEGUNDOS:
            _xp_cooldowns[chave] = agora

            ganho = random.randint(XP_MIN_POR_MENSAGEM, XP_MAX_POR_MENSAGEM)
            xp_antes, xp_depois = await asyncio.to_thread(
                _xp_adicionar, message.guild.id, message.author.id, ganho
            )

            nivel_antes = _xp_calcular_nivel(xp_antes)
            nivel_depois = _xp_calcular_nivel(xp_depois)
            if nivel_depois > nivel_antes:
                await message.channel.send(f"{message.author.mention} subiu para o nivel **{nivel_depois}**!")

                cargo_id = CARGOS_POR_NIVEL.get(nivel_depois)
                if cargo_id:
                    cargo = message.guild.get_role(cargo_id)
                    if cargo is None:
                        print(f"Cargo {cargo_id} do nivel {nivel_depois} nao foi encontrado no servidor.")
                    else:
                        try:
                            await message.author.add_roles(cargo)
                        except discord.Forbidden:
                            print(f"Sem permissao para dar o cargo '{cargo.name}' (nivel {nivel_depois}).")

    if message.guild is None or client.user in message.mentions:
        await _responder_llm(message)


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


def _teste_velocidade() -> dict:
    resultado = subprocess.run(
        ["speedtest", "--accept-license", "--accept-gdpr", "--format=json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(resultado.stdout)


@tree.command(name="velocidade_server", description="Testa a velocidade da internet da maquina")
async def velocidade_server(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        dados = await asyncio.to_thread(_teste_velocidade)
    except Exception as erro:
        print(f"Erro no teste de velocidade: {erro}")
        await interaction.followup.send("Nao consegui rodar o teste de velocidade agora.")
        return

    download_mbps = dados["download"]["bandwidth"] * 8 / 1_000_000
    upload_mbps = dados["upload"]["bandwidth"] * 8 / 1_000_000
    ping_ms = dados["ping"]["latency"]

    embed = discord.Embed(title="Teste de velocidade", color=discord.Color.blue())
    embed.add_field(name="Medida", value="Download\nUpload\nPing", inline=True)
    embed.add_field(
        name="Valor",
        value=f"{download_mbps:.1f} Mbps\n{upload_mbps:.1f} Mbps\n{ping_ms:.0f} ms",
        inline=True,
    )
    embed.set_footer(text=f"Servidor: {dados['server']['name']} ({dados['server']['location']})")
    await interaction.followup.send(embed=embed)


@tree.command(name="limpar", description="Apaga mensagens recentes deste canal")
@app_commands.describe(quantidade="Quantas mensagens apagar (1 a 100)")
@app_commands.default_permissions(manage_messages=True)
async def limpar(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.purge(limit=quantidade)
    await interaction.delete_original_response()


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

    ram_bot_mb = psutil.Process(os.getpid()).memory_info().rss // (1024**2)
    embed = discord.Embed(title="Monitoramento do servidor", color=discord.Color.blue())
    topicos = ["CPU", "RAM", "RAM do bot", "Disco"]
    valores = [
        f"{cpu}%",
        f"{ram.percent}% ({ram.used // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        f"{ram_bot_mb}MB",
        f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
    ]
    embed.add_field(name="Recurso", value="\n".join(topicos), inline=True)
    embed.add_field(name="Valor", value="\n".join(valores), inline=True)

    embed_servidores = discord.Embed(title="Servidores de Minecraft", color=discord.Color.green())
    topicos_srv = []
    valores_srv = []
    for config in MC_SERVERS.values():
        tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
        if tempo is None:
            valor = "Desligado"
        elif await asyncio.to_thread(_mc_servidor_pronto, config):
            valor = f"Ligado ha {tempo}"
        else:
            valor = f"Ligando... ({tempo})"
        topicos_srv.append(config["nome"])
        valores_srv.append(valor)
    embed_servidores.add_field(name="Servidor", value="\n".join(topicos_srv), inline=True)
    embed_servidores.add_field(name="Status", value="\n".join(valores_srv), inline=True)

    embed_llm = discord.Embed(title="LLM Local", color=discord.Color.purple())
    if await asyncio.to_thread(_llm_servidor_rodando):
        ram_mb = await asyncio.to_thread(_llm_ram_mb)
        topicos_llm = ["Status", "Modelo"]
        valores_llm = ["Ligada", LLM_MODELO]
        if ram_mb is not None:
            topicos_llm.append("RAM")
            valores_llm.append(f"{ram_mb}MB")
    else:
        topicos_llm = ["Status"]
        valores_llm = ["Desligada"]
    embed_llm.add_field(name="Info", value="\n".join(topicos_llm), inline=True)
    embed_llm.add_field(name="Valor", value="\n".join(valores_llm), inline=True)

    await interaction.followup.send(embeds=[embed, embed_servidores, embed_llm])


def _barra_progresso(atual: int, total: int, tamanho: int = 20) -> str:
    proporcao = min(atual / total, 1) if total else 0
    preenchido = round(tamanho * proporcao)
    return "█" * preenchido + "░" * (tamanho - preenchido)


@tree.command(name="rank", description="Mostra seu nivel e XP (ou de outra pessoa)")
@app_commands.describe(pessoa="De quem ver o rank (padrao: voce)")
async def rank(interaction: discord.Interaction, pessoa: discord.Member | None = None):
    alvo = pessoa or interaction.user
    await interaction.response.defer()

    xp_total = await asyncio.to_thread(_xp_obter, interaction.guild.id, alvo.id)
    nivel, xp_no_nivel, xp_necessario = _xp_progresso(xp_total)
    barra = _barra_progresso(xp_no_nivel, xp_necessario)

    usuario_completo = await client.fetch_user(alvo.id)
    cor = usuario_completo.accent_color or discord.Color.blue()

    embed = discord.Embed(title=f"Rank de {alvo.display_name}", color=cor)
    embed.set_thumbnail(url=alvo.display_avatar.url)
    embed.add_field(name="Nivel", value=str(nivel), inline=True)
    embed.add_field(name="XP total", value=str(xp_total), inline=True)
    embed.add_field(name="Progresso para o proximo nivel", value=f"{barra}\n{xp_no_nivel} / {xp_necessario} XP", inline=False)

    await interaction.followup.send(embed=embed)


@tree.command(name="leaderboard", description="Mostra os membros com mais XP no servidor")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()

    linhas = await asyncio.to_thread(_xp_leaderboard, interaction.guild.id, 10)
    if not linhas:
        await interaction.followup.send("Ainda ninguem tem XP neste servidor.")
        return

    texto = []
    for posicao, (user_id, xp_total) in enumerate(linhas, start=1):
        nivel, xp_no_nivel, xp_necessario = _xp_progresso(xp_total)
        barra = _barra_progresso(xp_no_nivel, xp_necessario, tamanho=10)
        texto.append(f"**{posicao}.** <@{user_id}> - Nivel {nivel} ({xp_total} XP)\n{barra}")

    embed = discord.Embed(title="Ranking do servidor", description="\n".join(texto), color=discord.Color.gold())
    await interaction.followup.send(embed=embed)


@tree.command(name="ajuda_xp", description="Explica como funciona o sistema de XP e niveis")
async def ajuda_xp(interaction: discord.Interaction):
    embed = discord.Embed(title="Como funciona o XP", color=discord.Color.blue())
    embed.add_field(
        name="Como ganhar XP",
        value=(
            f"Voce ganha entre {XP_MIN_POR_MENSAGEM} e {XP_MAX_POR_MENSAGEM} XP a cada mensagem enviada, "
            f"com um intervalo minimo de {XP_COOLDOWN_SEGUNDOS} segundos entre mensagens que contam."
        ),
        inline=False,
    )
    embed.add_field(
        name="Subindo de nivel",
        value=(
            "Cada nivel exige mais XP que o anterior. Quando voce sobe de nivel, "
            "o bot avisa no canal onde voce mandou a mensagem."
        ),
        inline=False,
    )
    embed.add_field(
        name="Cargos por nivel",
        value="Alguns niveis podem dar um cargo automatico, configurado pelo dono do bot.",
        inline=False,
    )
    embed.add_field(
        name="Comandos",
        value="`/rank` mostra seu nivel e progresso. `/leaderboard` mostra o ranking do servidor.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


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


@tree.command(name="charme", description="Joga um charme (com gif de anime) para alguem")
@app_commands.describe(pessoa="Para quem jogar o charme")
async def charme(interaction: discord.Interaction, pessoa: discord.Member):
    await interaction.response.defer()

    gif_url = await _buscar_gif("anime flirt")
    if gif_url is None:
        await interaction.followup.send("Nao encontrei nenhum gif de charme agora.")
        return

    embed = discord.Embed()
    embed.set_image(url=gif_url)
    await interaction.followup.send(
        content=f"{interaction.user.mention} jogou um charme para {pessoa.mention}", embed=embed
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


def _algum_mc_servidor_rodando() -> bool:
    return any(_mc_servidor_rodando(config) for config in MC_SERVERS.values())


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
        return f"\n**Conecte em:** {MC_HOST}:{porta}"
    return ""


@tree.command(name="mc_ligar", description="Liga um servidor de Minecraft")
@app_commands.describe(servidor="Qual servidor ligar")
@app_commands.choices(servidor=SERVIDOR_CHOICES)
async def mc_ligar(interaction: discord.Interaction, servidor: app_commands.Choice[str]):
    config = MC_SERVERS[servidor.value]
    await interaction.response.defer()

    if await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send(
            "Nao da pra ligar o servidor de Minecraft com a LLM local ligada - "
            "os dois juntos passariam do limite de RAM da maquina. Use /llm_desligar primeiro."
        )
        return

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


@tree.command(name="llm_ligar", description="Liga a LLM local (LM Studio)")
@app_commands.describe(modelo="Qual modelo carregar (padrao: o ultimo usado)")
@app_commands.autocomplete(modelo=_llm_autocomplete_modelo)
async def llm_ligar(interaction: discord.Interaction, modelo: str | None = None):
    global LLM_MODELO
    await interaction.response.defer()

    if await asyncio.to_thread(_algum_mc_servidor_rodando):
        await interaction.followup.send(
            "Nao da pra ligar a LLM local com um servidor de Minecraft ligado - "
            "os dois juntos passariam do limite de RAM da maquina. Desligue o servidor primeiro."
        )
        return

    if await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local ja esta ligada.")
        return

    modelo_escolhido = modelo or LLM_MODELO

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "load", modelo_escolhido, "-y"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao carregar modelo da LLM: {resultado.stderr}")
        await interaction.followup.send("Nao consegui carregar o modelo da LLM.")
        return

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "server", "start"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao iniciar servidor da LLM: {resultado.stderr}")
        await interaction.followup.send("Modelo carregado, mas nao consegui iniciar o servidor.")
        return

    LLM_MODELO = modelo_escolhido
    await interaction.followup.send(f"LLM local ligada com o modelo `{modelo_escolhido}` e pronta para uso.")


@tree.command(name="llm_desligar", description="Desliga a LLM local (LM Studio)")
async def llm_desligar(interaction: discord.Interaction):
    await interaction.response.defer()

    if not await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local ja esta desligada.")
        return

    await asyncio.to_thread(subprocess.run, [LMS_CLI, "server", "stop"], capture_output=True, text=True)
    await asyncio.to_thread(subprocess.run, [LMS_CLI, "unload", "--all"], capture_output=True, text=True)
    await interaction.followup.send("LLM local desligada.")


@tree.command(name="ajuda_llm", description="Explica como funciona a interacao com a LLM local")
async def ajuda_llm(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = discord.Embed(title="Como funciona a LLM local", color=discord.Color.purple())
    embed.add_field(
        name="Como conversar",
        value=(
            "Mencione o bot (`@nome_do_bot pergunta`) em qualquer canal, ou mande uma "
            "mensagem direta (DM) para ele. O bot responde usando a LLM rodando na "
            "maquina local, via LM Studio."
        ),
        inline=False,
    )
    embed.add_field(
        name="Memoria de conversa",
        value=(
            f"O bot lembra as ultimas {LLM_HISTORICO_MAX_MENSAGENS} mensagens de cada conversa "
            "(por canal + pessoa). Use `/llm_esquecer` para comecar uma conversa do zero."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ligar/desligar/trocar",
        value=(
            f"`/llm_ligar` carrega o modelo (`{LLM_MODELO}`) e liga o servidor local. "
            "`/llm_desligar` desliga tudo e libera a RAM usada. "
            "`/llm_trocar` troca o modelo sem desligar o servidor."
        ),
        inline=False,
    )

    modelos = await asyncio.to_thread(_llm_modelos_disponiveis)
    if modelos:
        linhas = [f"`{modelo}` - {LLM_DESCRICOES.get(modelo, 'Modelo geral.')}" for modelo in modelos]
        embed.add_field(name="Modelos disponiveis", value="\n".join(linhas), inline=False)

    embed.add_field(
        name="Se nao responder",
        value="A LLM local precisa estar ligada (`/llm_ligar`) para o bot conseguir responder. Confira com `/status`.",
        inline=False,
    )
    await interaction.followup.send(embed=embed)


@tree.command(name="llm_esquecer", description="Limpa a memoria da sua conversa atual com a LLM")
async def llm_esquecer(interaction: discord.Interaction):
    guild_id = interaction.guild.id if interaction.guild else None
    chave = (guild_id, interaction.channel.id, interaction.user.id)
    existia = _llm_historico.pop(chave, None) is not None

    if existia:
        await interaction.response.send_message("Memoria da conversa apagada.", ephemeral=True)
    else:
        await interaction.response.send_message("Voce ainda nao tinha conversa guardada aqui.", ephemeral=True)


@tree.command(name="llm_trocar", description="Troca o modelo da LLM local, sem desligar o servidor")
@app_commands.describe(modelo="Novo modelo a carregar")
@app_commands.autocomplete(modelo=_llm_autocomplete_modelo)
async def llm_trocar(interaction: discord.Interaction, modelo: str):
    global LLM_MODELO
    await interaction.response.defer()

    if not await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local esta desligada. Use /llm_ligar para escolher o modelo.")
        return

    await asyncio.to_thread(subprocess.run, [LMS_CLI, "unload", "--all"], capture_output=True, text=True)

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "load", modelo, "-y"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao carregar modelo da LLM: {resultado.stderr}")
        await interaction.followup.send(f"Nao consegui carregar o modelo `{modelo}`.")
        return

    LLM_MODELO = modelo
    await interaction.followup.send(f"Modelo trocado para `{modelo}`.")


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
