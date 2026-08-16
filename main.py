import asyncio
import json
import os
import platform
import subprocess
import time

import discord
import psutil
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import llm as llm_cog
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
    MC_SERVERS_DIR,
    STATUS_SEM_SERVIDOR,
    TOKEN,
)

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
    "bot.cogs.llm",
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




@bot.event
async def on_ready():
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
    cog_llm = bot.get_cog("LLM")
    if not cog_llm.verificar_llm_ociosa.is_running():
        cog_llm.verificar_llm_ociosa.start()
    cog_xp = bot.get_cog("XP")
    if not cog_xp.verificar_xp_semana.is_running():
        cog_xp.verificar_xp_semana.start()

    if llm_cog._llm_ultima_interacao is None and await asyncio.to_thread(llm_cog._llm_servidor_rodando):
        # A LLM ja estava ligada antes deste (re)inicio do bot (ex: restart durante
        # desenvolvimento) - conta a partir de agora, ja que nao ha como saber
        # de fato desde quando ela esta sem uso.
        llm_cog._llm_ultima_interacao = time.time()

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
    if await asyncio.to_thread(llm_cog._llm_servidor_rodando):
        ram_mb = await asyncio.to_thread(llm_cog._llm_ram_mb)
        parametros = await asyncio.to_thread(llm_cog._llm_parametros, llm_cog.LLM_MODELO)
        finalidade = llm_cog.LLM_DESCRICOES.get(llm_cog.LLM_MODELO, "Modelo geral.")

        topicos_llm = ["Status", "Modelo"]
        valores_llm = ["Ligada", llm_cog.LLM_MODELO]
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
        await llm_cog._llm_acao_ligar(interaction)

    @discord.ui.button(label="Desligar", style=discord.ButtonStyle.danger)
    async def desligar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await llm_cog._llm_acao_desligar(interaction)

    @discord.ui.button(label="Trocar modelo", style=discord.ButtonStyle.primary)
    async def trocar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        modelos = await asyncio.to_thread(llm_cog._llm_modelos_disponiveis)
        if not modelos:
            await interaction.response.send_message("Nenhum modelo encontrado.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Escolha o modelo para carregar:", view=llm_cog.SelecionarModeloLLM(modelos), ephemeral=True
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
