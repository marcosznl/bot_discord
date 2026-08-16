import asyncio
import json
import os
import re
import subprocess
import time

import discord
import psutil
from discord import app_commands
from discord.ext import commands, tasks
from mcrcon import MCRcon

from bot.config import (
    BASE_DIR,
    MC_AUTO_DESLIGAR_SEGUNDOS,
    MC_BOOT_CHECK_INTERVAL,
    MC_BOOT_TIMEOUT,
    MC_CHAT_CHANNEL_ID,
    MC_HOST,
    MC_RCON_SENHA,
    MC_SERVERS_DIR,
    MC_SHUTDOWN_TIMEOUT,
)

# Duplicado de proposito (tambem existe em bot/cogs/llm.py): sao poucas linhas
# e assim mc.py e llm.py continuam independentes um do outro, sem precisar
# importar de um para o outro so por causa dessa checagem de RAM.
RAM_MARGEM_SEGURANCA_GB = 0.5


def _ram_disponivel_gb() -> float:
    return psutil.virtual_memory().available / (1024**3)


def _ram_suficiente_para(necessaria_gb: float) -> tuple[bool, float]:
    disponivel = _ram_disponivel_gb()
    return disponivel >= necessaria_gb + RAM_MARGEM_SEGURANCA_GB, disponivel


def _formatar_duracao(segundos: int) -> str:
    horas, segundos_restantes = divmod(segundos, 3600)
    minutos, _ = divmod(segundos_restantes, 60)
    if horas:
        return f"{horas}h{minutos}m"
    return f"{minutos}m"


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
MC_CONFIG_PATH = os.path.join(BASE_DIR, "mc_config.json")

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


_mc_vazio_desde: dict[str, float] = {}
_mc_estava_rodando: dict[str, bool] = {}
_mc_parada_esperada: set[str] = set()

MC_CHAT_LINE_RE = re.compile(r"\]: (.+)$")
MC_CHAT_MSG_RE = re.compile(r"^<([^>]+)> (.+)$")
MC_CHAT_JOIN_RE = re.compile(r"^(\S+) joined the game$")
MC_CHAT_LEAVE_RE = re.compile(r"^(\S+) left the game$")

_mc_log_offsets: dict[str, int] = {}
_mc_log_inodes: dict[str, int] = {}


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


async def _mc_avisar_crash(bot: commands.Bot, config: dict) -> None:
    print(f"{config['nome']} parou de responder inesperadamente (possivel crash).")
    if MC_CHAT_CHANNEL_ID:
        canal = bot.get_channel(MC_CHAT_CHANNEL_ID)
        if canal is not None:
            await canal.send(
                f"⚠️ {config['nome']} parou de responder inesperadamente - pode ter crashado. "
                "Confira com `/mc` e ligue de novo se precisar."
            )


class Minecraft(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.monitorar_chat_minecraft.cancel()
        self.verificar_mc_ocioso.cancel()

    @tasks.loop(seconds=2)
    async def monitorar_chat_minecraft(self):
        if not MC_CHAT_CHANNEL_ID:
            return

        canal = self.bot.get_channel(MC_CHAT_CHANNEL_ID)
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

    @tasks.loop(seconds=60)
    async def verificar_mc_ocioso(self):
        agora = time.time()
        servidores = await asyncio.to_thread(_mc_servers_atual)
        for chave, config in servidores.items():
            rodando = await asyncio.to_thread(_mc_servidor_rodando, config)
            if _mc_estava_rodando.get(chave) and not rodando:
                if chave in _mc_parada_esperada:
                    _mc_parada_esperada.discard(chave)
                else:
                    await _mc_avisar_crash(self.bot, config)
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
                canal = self.bot.get_channel(MC_CHAT_CHANNEL_ID)
                if canal is not None:
                    await canal.send(f"{config['nome']} foi desligado automaticamente por ficar {minutos} minutos sem jogadores.")

    @app_commands.command(name="mc", description="Controla servidores de Minecraft")
    @app_commands.describe(servidor="Qual servidor")
    @app_commands.autocomplete(servidor=_mc_autocomplete_servidor)
    async def mc(self, interaction: discord.Interaction, servidor: str):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Minecraft(bot))
