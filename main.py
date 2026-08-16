import asyncio
import json
import os
import platform
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

from bot.cogs.minecraft import (
    _mc_acao_desligar,
    _mc_acao_ligar,
    _mc_ram_mb,
    _mc_servers_atual,
    _mc_servidor_pronto,
    _mc_tempo_ligado,
    _mc_jogadores_online,
)
from bot.config import (
    LLM_AUTO_DESLIGAR_SEGUNDOS,
    LLM_HISTORICO_MAX_MENSAGENS,
    LLM_MODELO,
    LLM_PROMPT_SISTEMA,
    LMS_CLI,
    LM_STUDIO_URL,
    MC_SERVERS_DIR,
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

BOT_INICIO = time.time()

intents = discord.Intents.default()
intents.members = True  # necessario para o evento de boas-vindas (on_member_join)

# Cogs de feature, carregados um a um conforme vao sendo extraidos de main.py.
EXTENSOES = [
    "bot.cogs.boasvindas",
    "bot.cogs.diversao",
    "bot.cogs.musica",
    "bot.cogs.playlist",
    "bot.cogs.xp",
    "bot.cogs.minecraft",
]


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

    if message.guild is None or bot.user in message.mentions:
        await _responder_llm(message)


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


@bot.event
async def on_ready():
    global _llm_ultima_interacao
    if not atualizar_presence.is_running():
        atualizar_presence.start()
    cog_musica = bot.get_cog("Musica")
    if not cog_musica.verificar_ocioso_voz.is_running():
        cog_musica.verificar_ocioso_voz.start()
    cog_mc = bot.get_cog("Minecraft")
    if not cog_mc.monitorar_chat_minecraft.is_running():
        cog_mc.monitorar_chat_minecraft.start()
    if not cog_mc.verificar_mc_ocioso.is_running():
        cog_mc.verificar_mc_ocioso.start()
    if not verificar_llm_ociosa.is_running():
        verificar_llm_ociosa.start()
    cog_xp = bot.get_cog("XP")
    if not cog_xp.verificar_xp_semana.is_running():
        cog_xp.verificar_xp_semana.start()

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
