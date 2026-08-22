import asyncio
import json
import logging
import re
import subprocess
import time

import aiohttp
import discord
import psutil
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import (
    LLM_AUTO_DESLIGAR_SEGUNDOS,
    LLM_HISTORICO_MAX_MENSAGENS,
    LLM_MODELO,
    LLM_PROMPT_SISTEMA,
    LMS_CLI,
    LM_STUDIO_URL,
)

logger = logging.getLogger(__name__)

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
_llm_lock_auto_ligar = asyncio.Lock()


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


def _llm_modelos_info() -> list[dict]:
    resultado = subprocess.run([LMS_CLI, "ls", "--llm", "--json"], capture_output=True, text=True)
    try:
        return json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return []


def _llm_modelos_disponiveis() -> list[str]:
    return [m["modelKey"] for m in _llm_modelos_info()]


def _llm_parametros(modelo: str) -> str | None:
    for info in _llm_modelos_info():
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


async def _llm_carregar_modelo(modelo: str) -> bool:
    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "load", modelo, "-y"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        logger.error(f"Erro ao carregar modelo da LLM: {resultado.stderr}")
        return False
    return True


async def _llm_iniciar_servidor() -> bool:
    resultado = await asyncio.to_thread(
        subprocess.run, [LMS_CLI, "server", "start"], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        logger.error(f"Erro ao iniciar servidor da LLM: {resultado.stderr}")
        return False
    return True


async def _llm_auto_ligar() -> tuple[bool, str]:
    if not await _llm_carregar_modelo(LLM_MODELO):
        return False, "Nao consegui ligar a LLM local agora."

    if not await _llm_iniciar_servidor():
        return False, "Modelo carregado, mas nao consegui ligar o servidor da LLM."

    return True, ""


async def _responder_llm(bot: commands.Bot, message: discord.Message) -> None:
    global _llm_ultima_interacao

    pergunta = message.content
    if bot.user in message.mentions:
        pergunta = re.sub(rf"<@!?{bot.user.id}>", "", pergunta).strip()

    if not pergunta:
        return

    if not await asyncio.to_thread(_llm_servidor_rodando):
        async with _llm_lock_auto_ligar:
            # Confere de novo depois de pegar o lock - outra mensagem pode ja
            # ter ligado a LLM enquanto essa esperava, evitando dois "load"/
            # "server start" concorrentes na mesma instancia do LM Studio.
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
            logger.error(f"Erro ao consultar LLM local: {erro}")
            await message.channel.send("Nao consegui falar com a LLM local agora. Ela esta rodando?")
            historico.pop()
            return

    historico.append({"role": "assistant", "content": resposta})
    del historico[:-LLM_HISTORICO_MAX_MENSAGENS]

    for inicio in range(0, len(resposta), 1900):
        await message.channel.send(resposta[inicio : inicio + 1900])


async def _llm_acao_ligar(interaction: discord.Interaction, modelo: str | None = None) -> None:
    global LLM_MODELO, _llm_ultima_interacao
    await interaction.response.defer()

    if await asyncio.to_thread(_llm_servidor_rodando):
        await interaction.followup.send("A LLM local ja esta ligada.")
        return

    modelo_escolhido = modelo or LLM_MODELO

    if not await _llm_carregar_modelo(modelo_escolhido):
        await interaction.followup.send("Nao consegui carregar o modelo da LLM.")
        return

    if not await _llm_iniciar_servidor():
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

    if not await _llm_carregar_modelo(modelo):
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


class LLM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.verificar_llm_ociosa.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.guild is None or self.bot.user in message.mentions:
            await _responder_llm(self.bot, message)

    @tasks.loop(seconds=60)
    async def verificar_llm_ociosa(self):
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
        logger.info(f"LLM local desligada automaticamente por ficar {minutos} minutos sem uso.")

    @app_commands.command(name="llm", description="Controla a LLM local")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def llm(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "O que voce quer fazer com a LLM local?", view=BotoesLLM(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LLM(bot))
