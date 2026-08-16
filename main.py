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
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks
from mcrcon import MCRcon

from bot.config import (
    LLM_AUTO_DESLIGAR_SEGUNDOS,
    LLM_HISTORICO_MAX_MENSAGENS,
    LLM_MODELO,
    LLM_PROMPT_SISTEMA,
    LMS_CLI,
    LM_STUDIO_URL,
    MC_AUTO_DESLIGAR_SEGUNDOS,
    MC_BOOT_CHECK_INTERVAL,
    MC_BOOT_TIMEOUT,
    MC_CHAT_CHANNEL_ID,
    MC_HOST,
    MC_RCON_SENHA,
    MC_SERVERS_DIR,
    MC_SHUTDOWN_TIMEOUT,
    STATUS_SEM_SERVIDOR,
    TOKEN,
)

# Descricao curta do ponto forte de cada modelo, usada pelo /ajuda_llm.
# Modelos baixados sem entrada aqui aparecem com uma descricao generica.
LLM_DESCRICOES = {
    "qwen/qwen3.5-9b": "Generalista forte, bom em varios idiomas e uso de ferramentas, contexto bem grande.",
    "google/gemma-4-12b": "Modelo maior do Google, bom para tarefas gerais e consegue analisar imagens.",
    "deepseek/deepseek-r1-0528-qwen3-8b": "Focado em raciocinio - melhor para logica, matematica e problemas passo a passo.",
    "microsoft/phi-4-mini-reasoning": "Pequeno e leve, tambem focado em raciocinio - ideal para economizar RAM.",
    "google/gemma-4-e4b": "Generalista leve do Google, tambem analisa imagens - bom modelo padrao do dia a dia.",
}

# Modelos que sabem usar "function calling" - so eles recebem a ferramenta de busca.
LLM_MODELOS_COM_FERRAMENTAS = {"qwen/qwen3.5-9b", "google/gemma-4-e4b"}

LLM_FERRAMENTA_BUSCA = {
    "type": "function",
    "function": {
        "name": "buscar_na_internet",
        "description": (
            "Busca informacoes atuais na internet. Use quando a pergunta exigir "
            "dados recentes ou algo que voce nao sabe."
        ),
        "parameters": {
            "type": "object",
            "properties": {"consulta": {"type": "string", "description": "O que buscar"}},
            "required": ["consulta"],
        },
    },
}

_llm_historico: dict[tuple[int | None, int, int], list[dict]] = {}
_llm_ultima_interacao: float | None = None

# Servidores de Minecraft conhecidos/configurados manualmente. Qualquer outra
# pasta dentro de MC_SERVERS_DIR que tenha um server.properties e descoberta
# automaticamente em tempo real por _mc_servers_atual() - nao precisa mexer
# aqui para adicionar/remover um servidor, so criar ou apagar a pasta.
MC_SERVERS = {
    "ftbskies2": {
        "nome": "ftbskies2",
        "dir": os.path.join(MC_SERVERS_DIR, "ftbskies2"),
        "comando": ["./run.sh"],
        "rcon_port": 25585,
        "ram_gb": 4,  # -Xmx4G em user_jvm_args.txt
    },
    "vanilla": {
        "nome": "vanilla",
        "dir": os.path.join(MC_SERVERS_DIR, "vanilla"),
        "comando": ["java", "-Xmx3G", "-jar", "run.jar", "nogui"],
        "rcon_port": 25586,
        "ram_gb": 3,  # -Xmx3G no proprio comando
    },
}

# Guarda o "ram_gb" editado pelo botao Editar do /mc e a config dos servidores
# descobertos automaticamente, para sobreviver a reinicios do bot.
MC_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc_config.json")

MC_JARS_PREFERIDOS = ("server.jar", "run.jar", "paper.jar", "purpur.jar", "spigot.jar")


def _mc_config_bruta() -> dict:
    try:
        with open(MC_CONFIG_PATH, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _mc_persistir_ajuste(chave: str, ajustes: dict) -> None:
    dados = _mc_config_bruta()
    dados.setdefault(chave, {}).update(ajustes)
    with open(MC_CONFIG_PATH, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=2)


def _mc_inferir_comando(caminho: str) -> list[str] | None:
    if os.path.isfile(os.path.join(caminho, "run.sh")):
        return ["./run.sh"]

    try:
        arquivos = os.listdir(caminho)
    except FileNotFoundError:
        return None

    jars = [f for f in arquivos if f.lower().endswith(".jar")]
    if not jars:
        return None

    preferidos = [j for j in jars if j.lower() in MC_JARS_PREFERIDOS]
    jar = preferidos[0] if preferidos else sorted(jars)[0]
    return ["java", "-Xmx2G", "-jar", jar, "nogui"]


def _mc_servers_atual() -> dict:
    persistido = _mc_config_bruta()
    servidores = {}

    for chave, config in MC_SERVERS.items():
        servidores[chave] = dict(config)
        if chave in persistido and "ram_gb" in persistido[chave]:
            servidores[chave]["ram_gb"] = persistido[chave]["ram_gb"]

    portas_usadas = {cfg["rcon_port"] for cfg in servidores.values() if cfg.get("rcon_port")}

    # Reaproveita servidores descobertos em varreduras anteriores, se a pasta ainda existir.
    for chave, valores in persistido.items():
        if chave in servidores or not valores.get("descoberto"):
            continue
        caminho = os.path.join(MC_SERVERS_DIR, chave)
        if not os.path.isdir(caminho):
            continue
        servidores[chave] = {
            "nome": valores["nome"],
            "dir": caminho,
            "comando": valores["comando"],
            "rcon_port": valores["rcon_port"],
            "ram_gb": valores.get("ram_gb", 2),
            "descoberto": True,
        }
        portas_usadas.add(valores["rcon_port"])

    for chave, config in servidores.items():
        config["chave"] = chave

    if not os.path.isdir(MC_SERVERS_DIR):
        return servidores

    proxima_porta = 25585
    for nome_pasta in sorted(os.listdir(MC_SERVERS_DIR)):
        if nome_pasta in servidores:
            continue
        caminho = os.path.join(MC_SERVERS_DIR, nome_pasta)
        if not os.path.isdir(caminho) or not os.path.isfile(os.path.join(caminho, "server.properties")):
            continue

        comando = _mc_inferir_comando(caminho)
        if comando is None:
            continue

        while proxima_porta in portas_usadas:
            proxima_porta += 1
        portas_usadas.add(proxima_porta)

        config = {
            "nome": nome_pasta,
            "dir": caminho,
            "comando": comando,
            "rcon_port": proxima_porta,
            "ram_gb": 2,
            "descoberto": True,
            "chave": nome_pasta,
        }
        _mc_garantir_rcon(config)
        servidores[nome_pasta] = config
        _mc_persistir_ajuste(
            nome_pasta,
            {"nome": config["nome"], "comando": config["comando"], "rcon_port": config["rcon_port"], "descoberto": True},
        )

    return servidores

# Categorias mostradas pelo /ajuda. Atualizar aqui sempre que um comando for
# criado, renomeado ou removido (as descricoes vem direto de cada comando).
COMANDOS_CATEGORIAS = {
    "Geral": ["ping", "status", "ajuda"],
    "Moderacao": ["limpar", "boasvindas"],
    "Niveis": ["xp"],
    "Diversao": ["gif", "socao", "abraco", "charme"],
    "Musica": ["musica", "playlist"],
    "Minecraft": ["mc"],
    "LLM": ["llm"],
    "Administracao (dono)": ["desligar", "reiniciar", "velocidade_server"],
}

XP_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xp.db")
XP_COOLDOWN_SEGUNDOS = 20
XP_MIN_POR_MENSAGEM = 15
XP_MAX_POR_MENSAGEM = 25

_xp_cooldowns: dict[tuple[int, int], float] = {}

_mc_vazio_desde: dict[str, float] = {}
_mc_estava_rodando: dict[str, bool] = {}
_mc_parada_esperada: set[str] = set()

MC_CHAT_LINE_RE = re.compile(r"\]: (.+)$")
MC_CHAT_MSG_RE = re.compile(r"^<([^>]+)> (.+)$")
MC_CHAT_JOIN_RE = re.compile(r"^(\S+) joined the game$")
MC_CHAT_LEAVE_RE = re.compile(r"^(\S+) left the game$")

_mc_log_offsets: dict[str, int] = {}
_mc_log_inodes: dict[str, int] = {}

BOT_INICIO = time.time()

intents = discord.Intents.default()
intents.members = True  # necessario para o evento de boas-vindas (on_member_join)

# Cogs de feature, carregados um a um conforme vao sendo extraidos de main.py.
EXTENSOES = ["bot.cogs.boasvindas", "bot.cogs.diversao", "bot.cogs.musica", "bot.cogs.playlist"]


class ArcoBot(commands.Bot):
    async def setup_hook(self) -> None:
        for extensao in EXTENSOES:
            await self.load_extension(extensao)


bot = ArcoBot(command_prefix=commands.when_mentioned, intents=intents)
tree = bot.tree


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
        "op": bot.ws.PRESENCE,
        "d": {
            "activities": [a.to_dict() for a in atividades],
            "afk": False,
            "since": 0,
            "status": "online",
        },
    }
    await bot.ws.send(json.dumps(payload))


@tasks.loop(seconds=60)
async def atualizar_presence():
    atividade_jogo = discord.Game(name=f"{STATUS_SEM_SERVIDOR} {_bot_tempo_ligado()}")
    atividade_custom = discord.CustomActivity(name=atividade_jogo.name)
    await _change_presence_multipla([atividade_jogo, atividade_custom])


@tasks.loop(seconds=2)
async def monitorar_chat_minecraft():
    if not MC_CHAT_CHANNEL_ID:
        return

    canal = bot.get_channel(MC_CHAT_CHANNEL_ID)
    if canal is None:
        return

    servidores = await asyncio.to_thread(_mc_servers_atual)
    prefixar = len(servidores) > 1
    for chave, config in servidores.items():
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
    colunas = [linha[1] for linha in conexao.execute("PRAGMA table_info(xp_usuarios)")]
    if "xp_semana" not in colunas:
        conexao.execute("ALTER TABLE xp_usuarios ADD COLUMN xp_semana INTEGER NOT NULL DEFAULT 0")
    conexao.execute("CREATE TABLE IF NOT EXISTS xp_desativado (guild_id INTEGER PRIMARY KEY)")
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS xp_optout (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
        "PRIMARY KEY (guild_id, user_id))"
    )
    conexao.execute("CREATE TABLE IF NOT EXISTS xp_periodo (guild_id INTEGER PRIMARY KEY, semana TEXT)")
    colunas_periodo = [linha[1] for linha in conexao.execute("PRAGMA table_info(xp_periodo)")]
    if "inicio" in colunas_periodo:
        # Migra do esquema antigo (coluna "inicio" com NOT NULL, sem uso desde
        # que o reset semanal passou a ser baseado em semana ISO, nao em timestamp).
        conexao.execute("ALTER TABLE xp_periodo RENAME TO xp_periodo_antigo")
        conexao.execute("CREATE TABLE xp_periodo (guild_id INTEGER PRIMARY KEY, semana TEXT)")
        conexao.execute("INSERT INTO xp_periodo (guild_id, semana) SELECT guild_id, semana FROM xp_periodo_antigo")
        conexao.execute("DROP TABLE xp_periodo_antigo")
        conexao.commit()
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS xp_cargos (guild_id INTEGER NOT NULL, nivel INTEGER NOT NULL, "
        "role_id INTEGER NOT NULL, PRIMARY KEY (guild_id, nivel))"
    )
    return conexao


def _xp_ativo(guild_id: int) -> bool:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute(
            "SELECT 1 FROM xp_desativado WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return linha is None


def _xp_definir_ativo(guild_id: int, ativo: bool) -> None:
    with _xp_conectar_bd() as conexao:
        if ativo:
            conexao.execute("DELETE FROM xp_desativado WHERE guild_id = ?", (guild_id,))
        else:
            conexao.execute(
                "INSERT INTO xp_desativado (guild_id) VALUES (?) ON CONFLICT (guild_id) DO NOTHING",
                (guild_id,),
            )
        conexao.commit()


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
            "SELECT xp, xp_semana FROM xp_usuarios WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        xp_antes, xp_semana_antes = linha if linha else (0, 0)
        xp_depois = max(0, xp_antes + quantidade)
        xp_semana_depois = max(0, xp_semana_antes + quantidade)
        conexao.execute(
            "INSERT INTO xp_usuarios (guild_id, user_id, xp, xp_semana) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = excluded.xp, xp_semana = excluded.xp_semana",
            (guild_id, user_id, xp_depois, xp_semana_depois),
        )
        conexao.commit()
    return xp_antes, xp_depois


def _xp_posicao(guild_id: int, user_id: int) -> int | None:
    xp_total = _xp_obter(guild_id, user_id)
    if xp_total <= 0:
        return None
    with _xp_conectar_bd() as conexao:
        maiores = conexao.execute(
            "SELECT COUNT(*) FROM xp_usuarios WHERE guild_id = ? AND xp > ?", (guild_id, xp_total)
        ).fetchone()[0]
    return maiores + 1


def _xp_leaderboard_semana(guild_id: int, limite: int = 10) -> list[tuple[int, int]]:
    with _xp_conectar_bd() as conexao:
        linhas = conexao.execute(
            "SELECT user_id, xp_semana FROM xp_usuarios WHERE guild_id = ? AND xp_semana > 0 "
            "ORDER BY xp_semana DESC LIMIT ?",
            (guild_id, limite),
        ).fetchall()
    return linhas


def _xp_esta_fora(guild_id: int, user_id: int) -> bool:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute(
            "SELECT 1 FROM xp_optout WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
    return linha is not None


def _xp_alternar_optout(guild_id: int, user_id: int) -> bool:
    with _xp_conectar_bd() as conexao:
        ja_fora = conexao.execute(
            "SELECT 1 FROM xp_optout WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        if ja_fora:
            conexao.execute(
                "DELETE FROM xp_optout WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
            )
        else:
            conexao.execute(
                "INSERT INTO xp_optout (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id)
            )
        conexao.commit()
    return not ja_fora


def _xp_semana_atual() -> str:
    # Ano-semana no formato ISO 8601 (semana sempre comeca na segunda-feira).
    return time.strftime("%G-W%V", time.localtime())


def _xp_semana_guardada(guild_id: int) -> str | None:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute("SELECT semana FROM xp_periodo WHERE guild_id = ?", (guild_id,)).fetchone()
    return linha[0] if linha else None


def _xp_resetar_semana(guild_id: int) -> None:
    with _xp_conectar_bd() as conexao:
        conexao.execute("UPDATE xp_usuarios SET xp_semana = 0 WHERE guild_id = ?", (guild_id,))
        conexao.execute(
            "INSERT INTO xp_periodo (guild_id, semana) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET semana = excluded.semana",
            (guild_id, _xp_semana_atual()),
        )
        conexao.commit()


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


# Margem de seguranca somada a qualquer estimativa de RAM necessaria, para
# sobrar espaco para o sistema operacional e o proprio bot.
RAM_MARGEM_SEGURANCA_GB = 0.5


def _ram_disponivel_gb() -> float:
    return psutil.virtual_memory().available / (1024**3)


def _llm_tamanho_gb(modelo: str) -> float | None:
    resultado = subprocess.run([LMS_CLI, "ls", "--llm", "--json"], capture_output=True, text=True)
    try:
        modelos = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return None
    for info in modelos:
        if info.get("modelKey") == modelo:
            tamanho = info.get("sizeBytes")
            return tamanho / (1024**3) if tamanho else None
    return None


def _ram_suficiente_para(necessaria_gb: float) -> tuple[bool, float]:
    disponivel = _ram_disponivel_gb()
    return disponivel >= necessaria_gb + RAM_MARGEM_SEGURANCA_GB, disponivel


def _llm_modelos_disponiveis() -> list[str]:
    resultado = subprocess.run([LMS_CLI, "ls", "--llm", "--json"], capture_output=True, text=True)
    try:
        modelos = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return []
    return [m["modelKey"] for m in modelos]


def _llm_parametros(modelo: str) -> str | None:
    resultado = subprocess.run([LMS_CLI, "ls", "--llm", "--json"], capture_output=True, text=True)
    try:
        modelos = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return None
    for info in modelos:
        if info.get("modelKey") == modelo:
            return info.get("paramsString")
    return None


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


async def _buscar_na_internet(consulta: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://html.duckduckgo.com/html/", data={"q": consulta}, headers=headers
        ) as resp:
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    for item in soup.select(".result")[:5]:
        titulo_el = item.select_one(".result__title")
        snippet_el = item.select_one(".result__snippet")
        if titulo_el and snippet_el:
            resultados.append(f"{titulo_el.get_text(strip=True)}: {snippet_el.get_text(strip=True)}")

    return "\n".join(resultados) if resultados else "Nenhum resultado encontrado."


async def _consultar_llm_local(historico: list[dict]) -> str:
    mensagens = [{"role": "system", "content": LLM_PROMPT_SISTEMA}, *historico]
    usa_ferramentas = LLM_MODELO in LLM_MODELOS_COM_FERRAMENTAS

    for _ in range(3):
        payload = {"messages": mensagens, "temperature": 0.7}
        if usa_ferramentas:
            payload["tools"] = [LLM_FERRAMENTA_BUSCA]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LM_STUDIO_URL, json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                dados = await resp.json()

        mensagem = dados["choices"][0]["message"]
        chamadas = mensagem.get("tool_calls")
        if not chamadas:
            return mensagem["content"]

        mensagens.append(mensagem)
        for chamada in chamadas:
            argumentos = json.loads(chamada["function"]["arguments"])
            resultado_busca = await _buscar_na_internet(argumentos.get("consulta", ""))
            mensagens.append(
                {"role": "tool", "tool_call_id": chamada["id"], "content": resultado_busca}
            )

    return "Nao consegui gerar uma resposta apos consultar a internet."


async def _llm_auto_ligar() -> tuple[bool, str]:
    tamanho_gb = await asyncio.to_thread(_llm_tamanho_gb, LLM_MODELO)
    if tamanho_gb is not None:
        suficiente, disponivel = _ram_suficiente_para(tamanho_gb)
        if not suficiente:
            return False, (
                f"Nao ha RAM suficiente para ligar a LLM agora "
                f"(precisa de ~{tamanho_gb:.1f}GB, tem {disponivel:.1f}GB disponivel)."
            )

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "load", LLM_MODELO, "-y"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao carregar modelo da LLM (auto): {resultado.stderr}")
        return False, "Nao consegui ligar a LLM local agora."

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "server", "start"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao iniciar servidor da LLM (auto): {resultado.stderr}")
        return False, "Modelo carregado, mas nao consegui ligar o servidor da LLM."

    return True, ""


async def _responder_llm(message: discord.Message) -> None:
    global _llm_ultima_interacao

    pergunta = message.content
    if bot.user in message.mentions:
        pergunta = re.sub(rf"<@!?{bot.user.id}>", "", pergunta).strip()

    if not pergunta:
        return

    if not await asyncio.to_thread(_llm_servidor_rodando):
        async with message.channel.typing():
            sucesso, motivo = await _llm_auto_ligar()
        if not sucesso:
            await message.channel.send(motivo)
            return

    _llm_ultima_interacao = time.time()

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


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if (
        message.guild is not None
        and await asyncio.to_thread(_xp_ativo, message.guild.id)
        and not await asyncio.to_thread(_xp_esta_fora, message.guild.id, message.author.id)
    ):
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

                cargo_id = await asyncio.to_thread(_xp_cargo_do_nivel, message.guild.id, nivel_depois)
                if cargo_id:
                    cargo = message.guild.get_role(cargo_id)
                    if cargo is None:
                        print(f"Cargo {cargo_id} do nivel {nivel_depois} nao foi encontrado no servidor.")
                    else:
                        try:
                            await message.author.add_roles(cargo)
                        except discord.Forbidden:
                            print(f"Sem permissao para dar o cargo '{cargo.name}' (nivel {nivel_depois}).")

    if message.guild is None or bot.user in message.mentions:
        await _responder_llm(message)


async def _mc_avisar_crash(config: dict) -> None:
    print(f"{config['nome']} parou de responder inesperadamente (possivel crash).")
    if MC_CHAT_CHANNEL_ID:
        canal = bot.get_channel(MC_CHAT_CHANNEL_ID)
        if canal is not None:
            await canal.send(
                f"⚠️ {config['nome']} parou de responder inesperadamente - pode ter crashado. "
                "Confira com `/mc` e ligue de novo se precisar."
            )


@tasks.loop(seconds=60)
async def verificar_mc_ocioso():
    agora = time.time()
    servidores = await asyncio.to_thread(_mc_servers_atual)
    for chave, config in servidores.items():
        rodando = await asyncio.to_thread(_mc_servidor_rodando, config)
        if _mc_estava_rodando.get(chave) and not rodando:
            if chave in _mc_parada_esperada:
                _mc_parada_esperada.discard(chave)
            else:
                await _mc_avisar_crash(config)
        _mc_estava_rodando[chave] = rodando

        if not await asyncio.to_thread(_mc_servidor_pronto, config):
            _mc_vazio_desde.pop(chave, None)
            continue

        jogadores = await asyncio.to_thread(_mc_jogadores_conectados, config)
        if jogadores:
            _mc_vazio_desde.pop(chave, None)
            continue

        inicio = _mc_vazio_desde.setdefault(chave, agora)
        if agora - inicio < MC_AUTO_DESLIGAR_SEGUNDOS:
            continue

        _mc_vazio_desde.pop(chave, None)
        processo = await asyncio.to_thread(_mc_processo, config)
        if processo is None:
            continue

        _mc_parada_esperada.add(chave)
        processo.terminate()
        minutos = MC_AUTO_DESLIGAR_SEGUNDOS // 60
        print(f"{config['nome']} desligado automaticamente por ficar {minutos} minutos sem jogadores.")
        if MC_CHAT_CHANNEL_ID:
            canal = bot.get_channel(MC_CHAT_CHANNEL_ID)
            if canal is not None:
                await canal.send(f"{config['nome']} foi desligado automaticamente por ficar {minutos} minutos sem jogadores.")


@tasks.loop(seconds=60)
async def verificar_llm_ociosa():
    global _llm_ultima_interacao

    if _llm_ultima_interacao is None:
        return

    if not await asyncio.to_thread(_llm_servidor_rodando):
        _llm_ultima_interacao = None
        return

    if time.time() - _llm_ultima_interacao < LLM_AUTO_DESLIGAR_SEGUNDOS:
        return

    await asyncio.to_thread(subprocess.run, [LMS_CLI, "server", "stop"], capture_output=True, text=True)
    await asyncio.to_thread(subprocess.run, [LMS_CLI, "unload", "--all"], capture_output=True, text=True)
    _llm_ultima_interacao = None
    minutos = LLM_AUTO_DESLIGAR_SEGUNDOS // 60
    print(f"LLM local desligada automaticamente por ficar {minutos} minutos sem uso.")


@tasks.loop(hours=1)
async def verificar_xp_semana():
    semana_atual = _xp_semana_atual()
    for guild in bot.guilds:
        semana_guardada = await asyncio.to_thread(_xp_semana_guardada, guild.id)
        if semana_guardada != semana_atual:
            await asyncio.to_thread(_xp_resetar_semana, guild.id)


@bot.event
async def on_ready():
    global _llm_ultima_interacao
    if not atualizar_presence.is_running():
        atualizar_presence.start()
    if not monitorar_chat_minecraft.is_running():
        monitorar_chat_minecraft.start()
    cog_musica = bot.get_cog("Musica")
    if not cog_musica.verificar_ocioso_voz.is_running():
        cog_musica.verificar_ocioso_voz.start()
    if not verificar_mc_ocioso.is_running():
        verificar_mc_ocioso.start()
    if not verificar_llm_ociosa.is_running():
        verificar_llm_ociosa.start()
    if not verificar_xp_semana.is_running():
        verificar_xp_semana.start()

    if _llm_ultima_interacao is None and await asyncio.to_thread(_llm_servidor_rodando):
        # A LLM ja estava ligada antes deste (re)inicio do bot (ex: restart durante
        # desenvolvimento) - conta a partir de agora, ja que nao ha como saber
        # de fato desde quando ela esta sem uso.
        _llm_ultima_interacao = time.time()

    app_info = await bot.application_info()
    bot.owner_id = app_info.owner.id
    await tree.sync()
    print(f"Bot conectado como {bot.user}")


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
    if interaction.user.id != bot.owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

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
@app_commands.describe(quantidade="Quantas mensagens apagar (1 a 200)")
@app_commands.default_permissions(manage_messages=True)
async def limpar(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 200]):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.purge(limit=quantidade)
    await interaction.delete_original_response()


def _todos_comandos_registrados() -> dict[str, app_commands.Command]:
    resultado = {}
    for item in tree.get_commands():
        if isinstance(item, app_commands.Group):
            for sub in item.commands:
                resultado[f"{item.name} {sub.name}"] = sub
        elif isinstance(item, app_commands.Command):
            resultado[item.name] = item
    return resultado


@tree.command(name="ajuda", description="Mostra todos os comandos do bot, por categoria")
async def ajuda(interaction: discord.Interaction):
    comandos_por_nome = _todos_comandos_registrados()

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


async def _status_acao_maquina(interaction: discord.Interaction) -> None:
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
        f"{ram.percent}% ({(ram.total - ram.available) // (1024 ** 3)}GB / {ram.total // (1024 ** 3)}GB)",
        f"{ram_bot_mb}MB",
        f"{disk.percent}% ({disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB)",
    ]
    embed.add_field(name="Recurso", value="\n".join(topicos), inline=True)
    embed.add_field(name="Valor", value="\n".join(valores), inline=True)
    await interaction.followup.send(embed=embed, view=BotoesStatusMaquina())


class BotoesStatusMaquina(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Desligar bot", style=discord.ButtonStyle.danger)
    async def desligar_bot(self, interaction: discord.Interaction, botao: discord.ui.Button):
        if interaction.user.id != bot.owner_id:
            await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
            return
        await interaction.response.send_message("Desligando o bot...")
        await bot.close()

    @discord.ui.button(label="Reiniciar bot", style=discord.ButtonStyle.secondary)
    async def reiniciar_bot(self, interaction: discord.Interaction, botao: discord.ui.Button):
        if interaction.user.id != bot.owner_id:
            await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
            return
        await interaction.response.send_message("Reiniciando o bot...")
        await bot.close()


async def _status_acao_minecraft(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    servidores = await asyncio.to_thread(_mc_servers_atual)

    embed_servidores = discord.Embed(title="Servidores de Minecraft", color=discord.Color.green())
    for config in servidores.values():
        tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
        if tempo is None:
            embed_servidores.add_field(name=config["nome"], value="Desligado", inline=False)
            continue

        pronto = await asyncio.to_thread(_mc_servidor_pronto, config)
        linhas = [f"**Status:** {'Ligado ha ' + tempo if pronto else f'Ligando... ({tempo})'}"]

        ram_mb = await asyncio.to_thread(_mc_ram_mb, config)
        if ram_mb is not None:
            linhas.append(f"**RAM:** {ram_mb}MB")

        if pronto:
            jogadores = _mc_jogadores_online(config)
            if jogadores:
                linhas.append(f"**Jogadores:** {jogadores}")

        embed_servidores.add_field(name=config["nome"], value="\n".join(linhas), inline=False)

    if not servidores:
        embed_servidores.description = "Nenhum servidor encontrado em " + MC_SERVERS_DIR

    await interaction.followup.send(embed=embed_servidores, view=BotoesStatusMinecraft(servidores))


class BotoesStatusMinecraft(discord.ui.View):
    def __init__(self, servidores: dict):
        super().__init__(timeout=600)
        for config in servidores.values():
            self._adicionar_botoes(config)

    def _adicionar_botoes(self, config: dict) -> None:
        async def ligar(interaction: discord.Interaction, config=config):
            await _mc_acao_ligar(interaction, config)

        async def desligar(interaction: discord.Interaction, config=config):
            await _mc_acao_desligar(interaction, config)

        botao_ligar = discord.ui.Button(label=f"Ligar {config['nome']}", style=discord.ButtonStyle.success)
        botao_ligar.callback = ligar
        botao_desligar = discord.ui.Button(label=f"Desligar {config['nome']}", style=discord.ButtonStyle.danger)
        botao_desligar.callback = desligar

        self.add_item(botao_ligar)
        self.add_item(botao_desligar)


async def _status_acao_llm(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    embed_llm = discord.Embed(title="Inteligencia Artificial", color=discord.Color.purple())
    if await asyncio.to_thread(_llm_servidor_rodando):
        ram_mb = await asyncio.to_thread(_llm_ram_mb)
        parametros = await asyncio.to_thread(_llm_parametros, LLM_MODELO)
        finalidade = LLM_DESCRICOES.get(LLM_MODELO, "Modelo geral.")

        topicos_llm = ["Status", "Modelo"]
        valores_llm = ["Ligada", LLM_MODELO]
        if ram_mb is not None:
            topicos_llm.append("RAM")
            valores_llm.append(f"{ram_mb}MB")
        topicos_llm.append("Parametros")
        valores_llm.append(parametros or "?")
        topicos_llm.append("Finalidade")
        valores_llm.append(finalidade)
    else:
        topicos_llm = ["Status"]
        valores_llm = ["Desligada"]
    embed_llm.add_field(name="Info", value="\n".join(topicos_llm), inline=True)
    embed_llm.add_field(name="Valor", value="\n".join(valores_llm), inline=True)
    await interaction.followup.send(embed=embed_llm, view=BotoesStatusLLM())


class BotoesStatusLLM(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Ligar", style=discord.ButtonStyle.success)
    async def ligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_ligar(interaction)

    @discord.ui.button(label="Desligar", style=discord.ButtonStyle.danger)
    async def desligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_desligar(interaction)

    @discord.ui.button(label="Trocar modelo", style=discord.ButtonStyle.primary)
    async def trocar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        modelos = await asyncio.to_thread(_llm_modelos_disponiveis)
        if not modelos:
            await interaction.response.send_message("Nenhum modelo encontrado.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Escolha o modelo para carregar:", view=SelecionarModeloLLM(modelos), ephemeral=True
        )


class BotoesStatus(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Servidor", style=discord.ButtonStyle.primary)
    async def maquina(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_maquina(interaction)

    @discord.ui.button(label="Minecraft", style=discord.ButtonStyle.success)
    async def minecraft(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_minecraft(interaction)

    @discord.ui.button(label="LLM", style=discord.ButtonStyle.secondary)
    async def llm(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _status_acao_llm(interaction)


@tree.command(name="status", description="Mostra o status da maquina, servidores de Minecraft e LLM")
async def status(interaction: discord.Interaction):
    await interaction.response.send_message("Qual status voce quer ver?", view=BotoesStatus())


def _barra_progresso(atual: int, total: int, tamanho: int = 20) -> str:
    proporcao = min(atual / total, 1) if total else 0
    preenchido = round(tamanho * proporcao)
    return "█" * preenchido + "░" * (tamanho - preenchido)


async def _xp_acao_rank(interaction: discord.Interaction) -> None:
    alvo = interaction.user
    await interaction.response.defer()

    xp_total = await asyncio.to_thread(_xp_obter, interaction.guild.id, alvo.id)
    nivel, xp_no_nivel, xp_necessario = _xp_progresso(xp_total)
    barra = _barra_progresso(xp_no_nivel, xp_necessario)
    posicao = await asyncio.to_thread(_xp_posicao, interaction.guild.id, alvo.id)

    usuario_completo = await bot.fetch_user(alvo.id)
    cor = usuario_completo.accent_color or discord.Color.blue()

    embed = discord.Embed(title=f"Rank de {alvo.display_name}", color=cor)
    embed.set_thumbnail(url=alvo.display_avatar.url)
    embed.add_field(name="Posicao", value=f"#{posicao}" if posicao else "Sem XP ainda", inline=True)
    embed.add_field(name="Nivel", value=str(nivel), inline=True)
    embed.add_field(name="XP total", value=str(xp_total), inline=True)
    embed.add_field(name="Progresso para o proximo nivel", value=f"{barra}\n{xp_no_nivel} / {xp_necessario} XP", inline=False)

    await interaction.followup.send(embed=embed)


async def _xp_acao_leaderboard(interaction: discord.Interaction) -> None:
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


async def _xp_acao_leaderboard_semana(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    linhas = await asyncio.to_thread(_xp_leaderboard_semana, interaction.guild.id, 10)
    if not linhas:
        await interaction.followup.send("Ainda ninguem ganhou XP nesta semana.")
        return

    texto = [f"**{posicao}.** <@{user_id}> - {xp_semana} XP" for posicao, (user_id, xp_semana) in enumerate(linhas, start=1)]

    embed = discord.Embed(
        title="Ranking semanal do servidor", description="\n".join(texto), color=discord.Color.orange()
    )
    await interaction.followup.send(embed=embed)


async def _xp_acao_optout(interaction: discord.Interaction) -> None:
    fora = await asyncio.to_thread(_xp_alternar_optout, interaction.guild.id, interaction.user.id)
    texto = (
        "Voce nao vai mais ganhar XP neste servidor. Use o mesmo botao para voltar quando quiser."
        if fora
        else "Voce voltou a ganhar XP neste servidor."
    )
    await interaction.response.send_message(texto, ephemeral=True)


def _xp_resetar_servidor(guild_id: int) -> int:
    with _xp_conectar_bd() as conexao:
        cursor = conexao.execute("DELETE FROM xp_usuarios WHERE guild_id = ?", (guild_id,))
        conexao.execute(
            "INSERT INTO xp_periodo (guild_id, semana) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET semana = excluded.semana",
            (guild_id, _xp_semana_atual()),
        )
        conexao.commit()
        return cursor.rowcount


def _xp_cargo_definir(guild_id: int, nivel: int, role_id: int) -> None:
    with _xp_conectar_bd() as conexao:
        conexao.execute(
            "INSERT INTO xp_cargos (guild_id, nivel, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT (guild_id, nivel) DO UPDATE SET role_id = excluded.role_id",
            (guild_id, nivel, role_id),
        )
        conexao.commit()


def _xp_cargo_remover(guild_id: int, nivel: int) -> bool:
    with _xp_conectar_bd() as conexao:
        cursor = conexao.execute("DELETE FROM xp_cargos WHERE guild_id = ? AND nivel = ?", (guild_id, nivel))
        conexao.commit()
        return cursor.rowcount > 0


def _xp_cargos_listar(guild_id: int) -> list[tuple[int, int]]:
    with _xp_conectar_bd() as conexao:
        return conexao.execute(
            "SELECT nivel, role_id FROM xp_cargos WHERE guild_id = ? ORDER BY nivel", (guild_id,)
        ).fetchall()


def _xp_cargo_do_nivel(guild_id: int, nivel: int) -> int | None:
    with _xp_conectar_bd() as conexao:
        linha = conexao.execute(
            "SELECT role_id FROM xp_cargos WHERE guild_id = ? AND nivel = ?", (guild_id, nivel)
        ).fetchone()
    return linha[0] if linha else None


class ConfirmarResetXP(discord.ui.View):
    def __init__(self, guild_id: int, autor_id: int):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.autor_id = autor_id
        self.mensagem: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "So quem pediu o reset pode confirmar.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmar reset", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        removidos = await asyncio.to_thread(_xp_resetar_servidor, self.guild_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"XP resetado. {removidos} registro(s) apagado(s).", view=self
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Reset cancelado.", view=self)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.mensagem:
            try:
                await self.mensagem.edit(content="Tempo esgotado - reset cancelado.", view=self)
            except discord.HTTPException:
                pass


async def _xp_acao_dar_remover(interaction: discord.Interaction, guild_id: int, membro: discord.Member, valor: int) -> None:
    xp_antes, xp_depois = await asyncio.to_thread(_xp_adicionar, guild_id, membro.id, valor)
    nivel_antes = _xp_calcular_nivel(xp_antes)
    nivel_depois = _xp_calcular_nivel(xp_depois)

    sinal = "+" if valor >= 0 else ""
    texto = f"{sinal}{valor} XP para {membro.mention}. Total agora: {xp_depois} XP (nivel {nivel_depois})."
    if nivel_depois != nivel_antes:
        texto += f"\nNivel mudou de {nivel_antes} para {nivel_depois}."
    await interaction.response.send_message(texto, ephemeral=True)


class ModalQuantidadeXP(discord.ui.Modal, title="Dar/remover XP"):
    quantidade = discord.ui.TextInput(label="Quantidade (negativo para remover)", placeholder="100", max_length=10)

    def __init__(self, guild_id: int, membro: discord.Member):
        super().__init__()
        self.guild_id = guild_id
        self.membro = membro

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.quantidade).strip()
        try:
            valor = int(texto)
        except ValueError:
            await interaction.response.send_message("Informe um numero inteiro (pode ser negativo).", ephemeral=True)
            return
        await _xp_acao_dar_remover(interaction, self.guild_id, self.membro, valor)


class SelecionarUsuarioXP(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.select = discord.ui.UserSelect(placeholder="Escolha o membro")
        self.select.callback = self._escolhido
        self.add_item(self.select)

    async def _escolhido(self, interaction: discord.Interaction):
        membro = self.select.values[0]
        await interaction.response.send_modal(ModalQuantidadeXP(self.guild_id, membro))


class SelecionarCargoNivel(discord.ui.View):
    def __init__(self, guild_id: int, nivel: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.nivel = nivel
        self.select_cargo = discord.ui.RoleSelect(placeholder=f"Escolha o cargo para o nivel {nivel}")
        self.select_cargo.callback = self._escolhido
        self.add_item(self.select_cargo)

    async def _escolhido(self, interaction: discord.Interaction):
        cargo = self.select_cargo.values[0]
        await asyncio.to_thread(_xp_cargo_definir, self.guild_id, self.nivel, cargo.id)
        await interaction.response.send_message(
            f"Nivel {self.nivel} agora da o cargo {cargo.mention}.", ephemeral=True
        )


class ModalNivelCargo(discord.ui.Modal, title="Cargo por nivel"):
    nivel = discord.ui.TextInput(label="Nivel", placeholder="ex: 5", max_length=5)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.nivel).strip()
        if not texto.isdigit() or int(texto) <= 0:
            await interaction.response.send_message(
                "Informe um nivel valido (numero inteiro maior que 0).", ephemeral=True
            )
            return
        nivel = int(texto)
        await interaction.response.send_message(
            f"Escolha o cargo para o nivel {nivel}:",
            view=SelecionarCargoNivel(self.guild_id, nivel),
            ephemeral=True,
        )


class ModalRemoverCargoNivel(discord.ui.Modal, title="Remover cargo por nivel"):
    nivel = discord.ui.TextInput(label="Nivel a remover", placeholder="ex: 5", max_length=5)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.nivel).strip()
        if not texto.isdigit():
            await interaction.response.send_message("Informe um nivel valido.", ephemeral=True)
            return
        removido = await asyncio.to_thread(_xp_cargo_remover, self.guild_id, int(texto))
        if removido:
            await interaction.response.send_message(f"Cargo do nivel {texto} removido.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Nao havia cargo configurado para o nivel {texto}.", ephemeral=True
            )


class BotoesCargosXP(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id

    @discord.ui.button(label="Adicionar/Editar", style=discord.ButtonStyle.success)
    async def adicionar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalNivelCargo(self.guild_id))

    @discord.ui.button(label="Ver configurados", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, botao: discord.ui.Button):
        cargos = await asyncio.to_thread(_xp_cargos_listar, self.guild_id)
        if not cargos:
            await interaction.response.send_message(
                "Nenhum cargo configurado por nivel neste servidor.", ephemeral=True
            )
            return
        linhas = [f"Nivel {nivel}: <@&{role_id}>" for nivel, role_id in cargos]
        await interaction.response.send_message("\n".join(linhas), ephemeral=True)

    @discord.ui.button(label="Remover", style=discord.ButtonStyle.danger)
    async def remover(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalRemoverCargoNivel(self.guild_id))


class BotoesXPConfig(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id

    @discord.ui.button(label="Ativar", style=discord.ButtonStyle.success)
    async def ativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await asyncio.to_thread(_xp_definir_ativo, self.guild_id, True)
        await interaction.response.send_message("Sistema de XP reativado neste servidor.", ephemeral=True)

    @discord.ui.button(label="Desativar", style=discord.ButtonStyle.secondary)
    async def desativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await asyncio.to_thread(_xp_definir_ativo, self.guild_id, False)
        await interaction.response.send_message("Sistema de XP desativado neste servidor.", ephemeral=True)

    @discord.ui.button(label="Resetar", style=discord.ButtonStyle.danger)
    async def resetar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        view = ConfirmarResetXP(self.guild_id, interaction.user.id)
        await interaction.response.send_message(
            "Tem certeza que quer apagar TODO o XP e nivel de todo mundo neste servidor? "
            "Essa acao nao pode ser desfeita.",
            view=view,
            ephemeral=True,
        )
        view.mensagem = await interaction.original_response()

    @discord.ui.button(label="Dar/Remover XP", style=discord.ButtonStyle.primary)
    async def dar_remover(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_message(
            "Escolha o membro:", view=SelecionarUsuarioXP(self.guild_id), ephemeral=True
        )

    @discord.ui.button(label="Cargos por nivel", style=discord.ButtonStyle.primary)
    async def cargos(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_message(
            "O que voce quer fazer com os cargos por nivel?", view=BotoesCargosXP(self.guild_id), ephemeral=True
        )


async def _xp_acao_config(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Voce precisa estar em um servidor e ter a permissao Gerenciar Servidor para usar essa opcao.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "O que voce quer fazer com o sistema de XP deste servidor?",
        view=BotoesXPConfig(interaction.guild.id),
        ephemeral=True,
    )


async def _xp_acao_ajuda(interaction: discord.Interaction) -> None:
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
        value=(
            "Alguns niveis podem dar um cargo automatico. Quem tem Gerenciar Servidor "
            "configura isso em `/xp` > Configuracoes > Cargos por nivel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Leaderboard semanal",
        value=(
            "Alem do ranking geral, existe um ranking semanal que zera sozinho a cada 7 dias "
            "- boa chance de aparecer no topo mesmo comecando atras no total geral."
        ),
        inline=False,
    )
    embed.add_field(
        name="Nao quer ganhar XP?",
        value="Use o botao Sair/Entrar no XP para parar (ou voltar) de ganhar XP neste servidor, sem afetar ninguem mais.",
        inline=False,
    )
    embed.add_field(
        name="Comandos",
        value="Use `/xp` e os botoes Meu rank / Leaderboard para ver seu nivel e o ranking do servidor.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


class BotoesXP(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Meu rank", style=discord.ButtonStyle.primary)
    async def rank(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_rank(interaction)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary)
    async def leaderboard(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_leaderboard(interaction)

    @discord.ui.button(label="Leaderboard semanal", style=discord.ButtonStyle.secondary)
    async def leaderboard_semana(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_leaderboard_semana(interaction)

    @discord.ui.button(label="Sair/Entrar no XP", style=discord.ButtonStyle.secondary)
    async def optout(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_optout(interaction)

    @discord.ui.button(label="Ajuda", style=discord.ButtonStyle.secondary)
    async def ajuda(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_ajuda(interaction)

    @discord.ui.button(label="Configuracoes (admin)", style=discord.ButtonStyle.danger)
    async def config(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_config(interaction)


@tree.command(name="xp", description="Ve seu rank, o leaderboard do servidor ou configura o sistema de XP")
async def xp(interaction: discord.Interaction):
    await interaction.response.send_message(
        "O que voce quer fazer com o sistema de XP?", view=BotoesXP()
    )


def _mc_porta(config) -> str | None:
    try:
        with open(os.path.join(config["dir"], "server.properties"), "r", encoding="utf-8", errors="ignore") as arquivo:
            for linha in arquivo:
                if linha.startswith("server-port="):
                    return linha.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return None


# Propriedades do server.properties editaveis pelo botao "Editar" do /mc.
# Para adicionar mais no futuro, basta incluir a chave real do arquivo aqui
# e um campo correspondente em ModalEditarServidorMC.
MC_DIFICULDADES_VALIDAS = {"peaceful", "easy", "normal", "hard"}


def _mc_ler_propriedade(config, chave: str) -> str | None:
    try:
        with open(os.path.join(config["dir"], "server.properties"), "r", encoding="utf-8", errors="ignore") as arquivo:
            for linha in arquivo:
                if linha.startswith(f"{chave}="):
                    return linha.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return None


def _mc_escrever_propriedade(config, chave: str, valor: str) -> None:
    caminho = os.path.join(config["dir"], "server.properties")
    with open(caminho, "r", encoding="utf-8", errors="ignore") as arquivo:
        linhas = arquivo.readlines()

    for i, linha in enumerate(linhas):
        if linha.startswith(f"{chave}="):
            linhas[i] = f"{chave}={valor}\n"
            break
    else:
        linhas.append(f"{chave}={valor}\n")

    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.writelines(linhas)


def _mc_garantir_rcon(config: dict) -> None:
    porta_atual = _mc_ler_propriedade(config, "rcon.port")
    if (
        _mc_ler_propriedade(config, "enable-rcon") == "true"
        and porta_atual == str(config["rcon_port"])
        and _mc_ler_propriedade(config, "rcon.password")
    ):
        return

    _mc_escrever_propriedade(config, "enable-rcon", "true")
    _mc_escrever_propriedade(config, "rcon.password", MC_RCON_SENHA)
    _mc_escrever_propriedade(config, "rcon.port", str(config["rcon_port"]))


def _mc_definir_ram(config: dict, ram_gb: int) -> None:
    config["ram_gb"] = ram_gb

    # Servidores iniciados via script (ex: run.sh do Forge/NeoForge) tem a RAM
    # configurada nesse arquivo, nao no comando em si.
    caminho_jvm_args = os.path.join(config["dir"], "user_jvm_args.txt")
    if os.path.exists(caminho_jvm_args):
        with open(caminho_jvm_args, "r", encoding="utf-8", errors="ignore") as arquivo:
            linhas = arquivo.readlines()

        nova_linha = f"-Xmx{ram_gb}G\n"
        for i, linha in enumerate(linhas):
            if linha.strip().startswith("-Xmx"):
                linhas[i] = nova_linha
                break
        else:
            linhas.append(nova_linha)

        with open(caminho_jvm_args, "w", encoding="utf-8") as arquivo:
            arquivo.writelines(linhas)

    _mc_persistir_ajuste(config["chave"], {"ram_gb": ram_gb})


def _mc_comando_execucao(config: dict) -> list[str]:
    comando = list(config["comando"])
    ram_gb = config.get("ram_gb")
    if comando and comando[0] == "java" and ram_gb:
        comando = [parte for parte in comando if not parte.startswith("-Xmx")]
        comando.insert(1, f"-Xmx{ram_gb}G")
    return comando


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
    if not _mc_servidor_rodando(config):
        return False
    log_path = os.path.join(config["dir"], "logs", "latest.log")
    resultado = subprocess.run(["grep", "-m", "1", "Done (", log_path], capture_output=True, text=True)
    return resultado.returncode == 0


def _mc_tempo_ligado(config) -> str | None:
    processo = _mc_processo(config)
    if processo is None:
        return None
    return _formatar_duracao(int(time.time() - processo.create_time()))


def _mc_ram_mb(config) -> int | None:
    processo = _mc_processo(config)
    if processo is None:
        return None
    return processo.memory_info().rss // (1024**2)


def _mc_jogadores_online(config) -> str | None:
    try:
        resposta = _mc_rcon_comando(config, "list")
    except Exception as erro:
        print(f"Erro ao consultar jogadores via RCON em {config['nome']}: {erro}")
        return None
    return re.sub(r"§.", "", resposta).strip() or None


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


def _mc_jogadores_conectados(config) -> set[str]:
    log_path = os.path.join(config["dir"], "logs", "latest.log")
    conectados = set()
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as arquivo:
            for linha in arquivo:
                correspondencia = MC_CHAT_LINE_RE.search(linha)
                conteudo = correspondencia.group(1) if correspondencia else linha.strip()

                entrou = MC_CHAT_JOIN_RE.match(conteudo)
                if entrou:
                    conectados.add(entrou.group(1))
                    continue

                saiu = MC_CHAT_LEAVE_RE.match(conteudo)
                if saiu:
                    conectados.discard(saiu.group(1))
    except FileNotFoundError:
        pass
    return conectados


def _mc_info_conexao(config) -> str:
    porta = _mc_porta(config)
    if MC_HOST and porta:
        return f"\n**Conecte em:** {MC_HOST}:{porta}"
    return ""


def _mc_rcon_comando(config: dict, comando: str) -> str:
    with MCRcon("127.0.0.1", MC_RCON_SENHA, port=config["rcon_port"]) as mcr:
        return mcr.command(comando)


async def _mc_acao_comando(interaction: discord.Interaction, config: dict, comando: str) -> None:
    await interaction.response.defer()

    if not await asyncio.to_thread(_mc_servidor_pronto, config):
        await interaction.followup.send(f"{config['nome']} nao esta rodando ou ainda nao esta pronto para comandos.")
        return

    try:
        # Chamado direto (sem asyncio.to_thread): a lib mcrcon usa signal.alarm
        # para o timeout, que so funciona na thread principal.
        resposta = _mc_rcon_comando(config, comando)
    except Exception as erro:
        print(f"Erro ao executar comando RCON em {config['nome']}: {erro}")
        await interaction.followup.send(f"Nao consegui executar o comando em {config['nome']}.")
        return

    texto = re.sub(r"§.", "", resposta).strip() or "Comando executado (sem resposta)."
    await interaction.followup.send(f"**{config['nome']}** > `{comando}`\n{texto[:1900]}")


async def _mc_acao_editar(
    interaction: discord.Interaction,
    config: dict,
    dificuldade: str,
    nome_servidor: str,
    max_jogadores: str,
    online_mode: str,
    ram_gb: str,
) -> None:
    if dificuldade and dificuldade.lower() not in MC_DIFICULDADES_VALIDAS:
        await interaction.response.send_message(
            f"Dificuldade invalida. Use uma dessas: {', '.join(sorted(MC_DIFICULDADES_VALIDAS))}.", ephemeral=True
        )
        return

    if max_jogadores and not max_jogadores.isdigit():
        await interaction.response.send_message("Max jogadores precisa ser um numero.", ephemeral=True)
        return

    if online_mode and online_mode.lower() not in {"true", "false"}:
        await interaction.response.send_message("Online mode precisa ser 'true' ou 'false'.", ephemeral=True)
        return

    if ram_gb and not ram_gb.isdigit():
        await interaction.response.send_message("RAM em GB precisa ser um numero.", ephemeral=True)
        return

    alteracoes = []
    if dificuldade:
        await asyncio.to_thread(_mc_escrever_propriedade, config, "difficulty", dificuldade.lower())
        alteracoes.append(f"Dificuldade: {dificuldade.lower()}")
    if nome_servidor:
        await asyncio.to_thread(_mc_escrever_propriedade, config, "motd", nome_servidor)
        alteracoes.append(f"Nome do servidor: {nome_servidor}")
    if max_jogadores:
        await asyncio.to_thread(_mc_escrever_propriedade, config, "max-players", max_jogadores)
        alteracoes.append(f"Max jogadores: {max_jogadores}")
    if online_mode:
        await asyncio.to_thread(_mc_escrever_propriedade, config, "online-mode", online_mode.lower())
        alteracoes.append(f"Online mode: {online_mode.lower()}")
    if ram_gb:
        await asyncio.to_thread(_mc_definir_ram, config, int(ram_gb))
        alteracoes.append(f"RAM: {ram_gb}GB")

    if not alteracoes:
        await interaction.response.send_message("Nenhuma alteracao informada.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Configuracoes de **{config['nome']}** atualizadas:\n"
        + "\n".join(alteracoes)
        + "\n\nReinicie o servidor (botao Reiniciar) para essas mudancas terem efeito.",
        ephemeral=True,
    )


async def _mc_acao_ligar(interaction: discord.Interaction, config: dict) -> None:
    await interaction.response.defer()

    necessaria_gb = config.get("ram_gb", 2)
    suficiente, disponivel = await asyncio.to_thread(_ram_suficiente_para, necessaria_gb)
    if not suficiente:
        await interaction.followup.send(
            f"Nao ha RAM suficiente para ligar {config['nome']} agora "
            f"(precisa de ~{necessaria_gb}GB, tem {disponivel:.1f}GB disponivel)."
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
        _mc_comando_execucao(config),
        cwd=config["dir"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    mensagem = await interaction.followup.send(f"Ligando {config['nome']}, aguarde...", wait=True)

    for _ in range(MC_BOOT_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if await asyncio.to_thread(_mc_servidor_pronto, config):
            info = await asyncio.to_thread(_mc_info_conexao, config)
            await mensagem.edit(content=f"{config['nome']} ligado e pronto para conexoes!{info}")
            return

    await mensagem.edit(
        content=f"{config['nome']} esta demorando mais que o esperado para subir. Use /status para verificar."
    )


async def _mc_acao_desligar(interaction: discord.Interaction, config: dict) -> None:
    await interaction.response.defer()

    processo = await asyncio.to_thread(_mc_processo, config)
    if processo is None:
        await interaction.followup.send(f"{config['nome']} nao esta rodando.")
        return

    tempo = await asyncio.to_thread(_mc_tempo_ligado, config)
    _mc_parada_esperada.add(config["chave"])
    processo.terminate()

    mensagem = f"Desligando {config['nome']} (salvando o mundo)..."
    if tempo:
        mensagem += f"\nFicou ligado por {tempo}."
    await interaction.followup.send(mensagem)


async def _mc_acao_reiniciar(interaction: discord.Interaction, config: dict) -> None:
    await interaction.response.defer()

    processo = await asyncio.to_thread(_mc_processo, config)
    if processo is None:
        await interaction.followup.send(f"{config['nome']} nao esta rodando. Use o botao Ligar.")
        return

    mensagem = await interaction.followup.send(f"Reiniciando {config['nome']} (salvando o mundo)...", wait=True)
    _mc_parada_esperada.add(config["chave"])
    processo.terminate()

    for _ in range(MC_SHUTDOWN_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if not await asyncio.to_thread(_mc_servidor_rodando, config):
            break
    else:
        await mensagem.edit(
            content=f"{config['nome']} esta demorando para desligar. Tente novamente em instantes."
        )
        return

    subprocess.Popen(
        _mc_comando_execucao(config),
        cwd=config["dir"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    await mensagem.edit(content=f"{config['nome']} desligou, ligando de novo, aguarde...")

    for _ in range(MC_BOOT_TIMEOUT // MC_BOOT_CHECK_INTERVAL):
        await asyncio.sleep(MC_BOOT_CHECK_INTERVAL)
        if await asyncio.to_thread(_mc_servidor_pronto, config):
            info = await asyncio.to_thread(_mc_info_conexao, config)
            await mensagem.edit(content=f"{config['nome']} reiniciado e pronto para conexoes!{info}")
            return

    await mensagem.edit(
        content=f"{config['nome']} esta demorando mais que o esperado para subir. Use /status para verificar."
    )


class ModalComandoMC(discord.ui.Modal, title="Executar comando"):
    comando = discord.ui.TextInput(label="Comando (sem a barra)", placeholder="gamemode creative NomeDoJogador", max_length=200)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    async def on_submit(self, interaction: discord.Interaction):
        await _mc_acao_comando(interaction, self.config, str(self.comando))


class ModalEditarServidorMC(discord.ui.Modal):
    def __init__(self, config: dict):
        super().__init__(title=f"Editar {config['nome']}"[:45])
        self.config = config

        self.dificuldade = discord.ui.TextInput(
            label="Dificuldade (peaceful, easy, normal, hard)",
            default=_mc_ler_propriedade(config, "difficulty") or "",
            max_length=20,
            required=False,
        )
        self.nome_servidor = discord.ui.TextInput(
            label="Nome do servidor (motd)",
            default=_mc_ler_propriedade(config, "motd") or "",
            max_length=100,
            required=False,
        )
        self.max_jogadores = discord.ui.TextInput(
            label="Max jogadores",
            default=_mc_ler_propriedade(config, "max-players") or "",
            max_length=10,
            required=False,
        )
        self.online_mode = discord.ui.TextInput(
            label="Online mode (true ou false)",
            default=_mc_ler_propriedade(config, "online-mode") or "",
            max_length=5,
            required=False,
        )
        self.ram_gb = discord.ui.TextInput(
            label="RAM em GB",
            default=str(config.get("ram_gb", "")),
            max_length=3,
            required=False,
        )
        self.add_item(self.dificuldade)
        self.add_item(self.nome_servidor)
        self.add_item(self.max_jogadores)
        self.add_item(self.online_mode)
        self.add_item(self.ram_gb)

    async def on_submit(self, interaction: discord.Interaction):
        await _mc_acao_editar(
            interaction,
            self.config,
            str(self.dificuldade).strip(),
            str(self.nome_servidor).strip(),
            str(self.max_jogadores).strip(),
            str(self.online_mode).strip(),
            str(self.ram_gb).strip(),
        )


class BotoesMC(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=600)
        self.config = config

    @discord.ui.button(label="Ligar", style=discord.ButtonStyle.success)
    async def ligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _mc_acao_ligar(interaction, self.config)

    @discord.ui.button(label="Desligar", style=discord.ButtonStyle.danger)
    async def desligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _mc_acao_desligar(interaction, self.config)

    @discord.ui.button(label="Reiniciar", style=discord.ButtonStyle.secondary)
    async def reiniciar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _mc_acao_reiniciar(interaction, self.config)

    @discord.ui.button(label="Comando", style=discord.ButtonStyle.primary)
    async def comando(self, interaction: discord.Interaction, botao: discord.ui.Button):
        if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Voce precisa estar em um servidor e ter a permissao Gerenciar Servidor para usar essa opcao.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ModalComandoMC(self.config))

    @discord.ui.button(label="Editar", style=discord.ButtonStyle.secondary)
    async def editar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Voce precisa estar em um servidor e ter a permissao Gerenciar Servidor para usar essa opcao.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ModalEditarServidorMC(self.config))


async def _mc_autocomplete_servidor(interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[str]]:
    servidores = await asyncio.to_thread(_mc_servers_atual)
    atual = atual.lower()
    return [
        app_commands.Choice(name=config["nome"], value=chave)
        for chave, config in servidores.items()
        if atual in config["nome"].lower() or atual in chave.lower()
    ][:25]


@tree.command(name="mc", description="Controla servidores de Minecraft")
@app_commands.describe(servidor="Qual servidor")
@app_commands.autocomplete(servidor=_mc_autocomplete_servidor)
async def mc(interaction: discord.Interaction, servidor: str):
    servidores = await asyncio.to_thread(_mc_servers_atual)
    config = servidores.get(servidor)
    if config is None:
        await interaction.response.send_message(
            "Servidor nao encontrado. Escolha uma das opcoes sugeridas.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"O que voce quer fazer com o **{config['nome']}**?", view=BotoesMC(config)
    )


async def _llm_acao_ligar(interaction: discord.Interaction, modelo: str | None = None) -> None:
    global LLM_MODELO, _llm_ultima_interacao
    await interaction.response.defer()

    if await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local ja esta ligada.")
        return

    modelo_escolhido = modelo or LLM_MODELO

    tamanho_gb = await asyncio.to_thread(_llm_tamanho_gb, modelo_escolhido)
    if tamanho_gb is not None:
        suficiente, disponivel = _ram_suficiente_para(tamanho_gb)
        if not suficiente:
            await interaction.followup.send(
                f"Nao ha RAM suficiente para carregar `{modelo_escolhido}` agora "
                f"(precisa de ~{tamanho_gb:.1f}GB, tem {disponivel:.1f}GB disponivel)."
            )
            return

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
    _llm_ultima_interacao = time.time()
    await interaction.followup.send(f"LLM local ligada com o modelo `{modelo_escolhido}` e pronta para uso.")


async def _llm_acao_desligar(interaction: discord.Interaction) -> None:
    global _llm_ultima_interacao
    await interaction.response.defer()

    if not await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local ja esta desligada.")
        return

    await asyncio.to_thread(subprocess.run, [LMS_CLI, "server", "stop"], capture_output=True, text=True)
    await asyncio.to_thread(subprocess.run, [LMS_CLI, "unload", "--all"], capture_output=True, text=True)
    _llm_ultima_interacao = None
    await interaction.followup.send("LLM local desligada.")


async def _llm_acao_ajuda(interaction: discord.Interaction) -> None:
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
            "(por canal + pessoa). Use o botao Esquecer para comecar uma conversa do zero."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ligar/desligar/trocar",
        value=(
            f"O botao Ligar carrega o modelo (`{LLM_MODELO}`) e liga o servidor local. "
            "Desligar desliga tudo e libera a RAM usada. "
            "Trocar troca o modelo sem desligar o servidor."
        ),
        inline=False,
    )

    embed.add_field(
        name="Se demorar para responder",
        value=(
            f"A LLM liga sozinha quando voce fala com ela pela primeira vez (com o modelo `{LLM_MODELO}`), "
            "e desliga sozinha depois de um tempo sem uso. Se a primeira mensagem demorar mais, "
            "e porque o modelo esta carregando - so aguardar."
        ),
        inline=False,
    )
    await interaction.followup.send(embed=embed)


async def _llm_acao_esquecer(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild.id if interaction.guild else None
    chave = (guild_id, interaction.channel.id, interaction.user.id)
    existia = _llm_historico.pop(chave, None) is not None

    if existia:
        await interaction.response.send_message("Memoria da conversa apagada.", ephemeral=True)
    else:
        await interaction.response.send_message("Voce ainda nao tinha conversa guardada aqui.", ephemeral=True)


async def _llm_acao_trocar(interaction: discord.Interaction, modelo: str) -> None:
    global LLM_MODELO, _llm_ultima_interacao
    await interaction.response.defer()

    if not await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local esta desligada. Use o botao Ligar primeiro.")
        return

    await asyncio.to_thread(subprocess.run, [LMS_CLI, "unload", "--all"], capture_output=True, text=True)

    tamanho_gb = await asyncio.to_thread(_llm_tamanho_gb, modelo)
    if tamanho_gb is not None:
        suficiente, disponivel = _ram_suficiente_para(tamanho_gb)
        if not suficiente:
            await interaction.followup.send(
                f"Nao ha RAM suficiente para trocar para `{modelo}` "
                f"(precisa de ~{tamanho_gb:.1f}GB, tem {disponivel:.1f}GB disponivel)."
            )
            return

    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "load", modelo, "-y"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        print(f"Erro ao carregar modelo da LLM: {resultado.stderr}")
        await interaction.followup.send(f"Nao consegui carregar o modelo `{modelo}`.")
        return

    LLM_MODELO = modelo
    _llm_ultima_interacao = time.time()
    await interaction.followup.send(f"Modelo trocado para `{modelo}`.")


class SelecionarModeloLLM(discord.ui.View):
    def __init__(self, modelos: list[str]):
        super().__init__(timeout=600)
        select = discord.ui.Select(
            placeholder="Escolha o modelo",
            options=[discord.SelectOption(label=modelo, value=modelo) for modelo in modelos[:25]],
        )
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        modelo = interaction.data["values"][0]
        await _llm_acao_trocar(interaction, modelo)


class BotoesLLM(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Ligar", style=discord.ButtonStyle.success)
    async def ligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_ligar(interaction)

    @discord.ui.button(label="Desligar", style=discord.ButtonStyle.danger)
    async def desligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_desligar(interaction)

    @discord.ui.button(label="Trocar modelo", style=discord.ButtonStyle.primary)
    async def trocar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        modelos = await asyncio.to_thread(_llm_modelos_disponiveis)
        if not modelos:
            await interaction.response.send_message("Nenhum modelo encontrado.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Escolha o modelo para carregar:", view=SelecionarModeloLLM(modelos), ephemeral=True
        )

    @discord.ui.button(label="Esquecer conversa", style=discord.ButtonStyle.secondary)
    async def esquecer(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_esquecer(interaction)

    @discord.ui.button(label="Ajuda", style=discord.ButtonStyle.secondary)
    async def ajuda(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _llm_acao_ajuda(interaction)


@tree.command(name="llm", description="Controla a LLM local")
async def llm(interaction: discord.Interaction):
    await interaction.response.send_message(
        "O que voce quer fazer com a LLM local?", view=BotoesLLM(), ephemeral=True
    )


@tree.command(name="desligar", description="Desliga o bot (apenas o dono)")
async def desligar(interaction: discord.Interaction):
    if interaction.user.id != bot.owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

    await interaction.response.send_message("Desligando o bot...")
    await bot.close()


@tree.command(name="reiniciar", description="Reinicia o bot (apenas o dono)")
async def reiniciar(interaction: discord.Interaction):
    if interaction.user.id != bot.owner_id:
        await interaction.response.send_message("Voce nao tem permissao para isso.", ephemeral=True)
        return

    await interaction.response.send_message("Reiniciando o bot...")
    await bot.close()


if __name__ == "__main__":
    bot.run(TOKEN)
