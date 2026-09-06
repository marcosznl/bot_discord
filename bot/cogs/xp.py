import asyncio
import logging
import os
import random
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import sqlitestore
from bot.config import DATA_DIR
from bot.permissions import requer_manage_guild
from bot.retry_ui import ModalComRetry, ViewComRetry

logger = logging.getLogger(__name__)

XP_DB_PATH = os.path.join(DATA_DIR, "xp.db")
XP_COOLDOWN_SEGUNDOS = 20
XP_MIN_POR_MENSAGEM = 15
XP_MAX_POR_MENSAGEM = 25

_xp_cooldowns: dict[tuple[int, int], float] = {}


# Bancos ja preparados por _xp_garantir_schema (por caminho de arquivo) - evita
# refazer CREATE TABLE/PRAGMA/ALTER em toda mensagem so pra checar um schema
# que so muda quando o codigo muda.
_xp_schemas_prontos: set[str] = set()


def _xp_garantir_schema(conexao: sqlite3.Connection) -> None:
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS xp_usuarios ("
        "guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (guild_id, user_id))"
    )
    colunas = [linha[1] for linha in conexao.execute("PRAGMA table_info(xp_usuarios)")]
    if "xp_semana" not in colunas:
        conexao.execute("ALTER TABLE xp_usuarios ADD COLUMN xp_semana INTEGER NOT NULL DEFAULT 0")
    conexao.execute("CREATE TABLE IF NOT EXISTS xp_desativado (guild_id INTEGER PRIMARY KEY)")
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


def _xp_conectar_bd() -> sqlite3.Connection:
    conexao = sqlite3.connect(XP_DB_PATH)
    if XP_DB_PATH not in _xp_schemas_prontos:
        _xp_garantir_schema(conexao)
        _xp_schemas_prontos.add(XP_DB_PATH)
    return conexao


def _xp_ativo(guild_id: int) -> bool:
    linha = sqlitestore.consultar_um(
        _xp_conectar_bd, "SELECT 1 FROM xp_desativado WHERE guild_id = ?", (guild_id,)
    )
    return linha is None


def _xp_definir_ativo(guild_id: int, ativo: bool) -> None:
    if ativo:
        sqlitestore.executar(_xp_conectar_bd, "DELETE FROM xp_desativado WHERE guild_id = ?", (guild_id,))
    else:
        sqlitestore.executar(
            _xp_conectar_bd,
            "INSERT INTO xp_desativado (guild_id) VALUES (?) ON CONFLICT (guild_id) DO NOTHING",
            (guild_id,),
        )


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
    linha = sqlitestore.consultar_um(
        _xp_conectar_bd, "SELECT xp FROM xp_usuarios WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
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
    maiores = sqlitestore.consultar_um(
        _xp_conectar_bd, "SELECT COUNT(*) FROM xp_usuarios WHERE guild_id = ? AND xp > ?", (guild_id, xp_total)
    )[0]
    return maiores + 1


def _xp_leaderboard_semana(guild_id: int, limite: int = 10) -> list[tuple[int, int]]:
    return sqlitestore.consultar_todos(
        _xp_conectar_bd,
        "SELECT user_id, xp_semana FROM xp_usuarios WHERE guild_id = ? AND xp_semana > 0 "
        "ORDER BY xp_semana DESC LIMIT ?",
        (guild_id, limite),
    )


def _xp_semana_atual() -> str:
    # Ano-semana no formato ISO 8601 (semana sempre comeca na segunda-feira).
    return time.strftime("%G-W%V", time.localtime())


def _xp_semana_guardada(guild_id: int) -> str | None:
    linha = sqlitestore.consultar_um(_xp_conectar_bd, "SELECT semana FROM xp_periodo WHERE guild_id = ?", (guild_id,))
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
    return sqlitestore.consultar_todos(
        _xp_conectar_bd,
        "SELECT user_id, xp FROM xp_usuarios WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
        (guild_id, limite),
    )


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
    sqlitestore.executar(
        _xp_conectar_bd,
        "INSERT INTO xp_cargos (guild_id, nivel, role_id) VALUES (?, ?, ?) "
        "ON CONFLICT (guild_id, nivel) DO UPDATE SET role_id = excluded.role_id",
        (guild_id, nivel, role_id),
    )


def _xp_cargo_remover(guild_id: int, nivel: int) -> bool:
    return (
        sqlitestore.executar_retornando_rowcount(
            _xp_conectar_bd, "DELETE FROM xp_cargos WHERE guild_id = ? AND nivel = ?", (guild_id, nivel)
        )
        > 0
    )


def _xp_cargos_listar(guild_id: int) -> list[tuple[int, int]]:
    return sqlitestore.consultar_todos(
        _xp_conectar_bd, "SELECT nivel, role_id FROM xp_cargos WHERE guild_id = ? ORDER BY nivel", (guild_id,)
    )


def _xp_cargo_do_nivel(guild_id: int, nivel: int) -> int | None:
    linha = sqlitestore.consultar_um(
        _xp_conectar_bd, "SELECT role_id FROM xp_cargos WHERE guild_id = ? AND nivel = ?", (guild_id, nivel)
    )
    return linha[0] if linha else None


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

    usuario_completo = await interaction.client.fetch_user(alvo.id)
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


class ConfirmarResetXP(ViewComRetry):
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


class ModalQuantidadeXP(ModalComRetry, title="Dar/remover XP"):
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


class SelecionarUsuarioXP(ViewComRetry):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.select = discord.ui.UserSelect(placeholder="Escolha o membro")
        self.select.callback = self._escolhido
        self.add_item(self.select)

    async def _escolhido(self, interaction: discord.Interaction):
        membro = self.select.values[0]
        await interaction.response.send_modal(ModalQuantidadeXP(self.guild_id, membro))


class SelecionarCargoNivel(ViewComRetry):
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


class ModalNivelCargo(ModalComRetry, title="Cargo por nivel"):
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


class ModalRemoverCargoNivel(ModalComRetry, title="Remover cargo por nivel"):
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


class BotoesCargosXP(ViewComRetry):
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


class BotoesXPConfig(ViewComRetry):
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
    if not await requer_manage_guild(interaction):
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
        name="Comandos",
        value="Use `/xp` e os botoes Meu rank / Leaderboard para ver seu nivel e o ranking do servidor.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


class BotoesXP(ViewComRetry):
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

    @discord.ui.button(label="Ajuda", style=discord.ButtonStyle.secondary)
    async def ajuda(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_ajuda(interaction)

    @discord.ui.button(label="Configuracoes (admin)", style=discord.ButtonStyle.danger)
    async def config(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _xp_acao_config(interaction)


class XP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self) -> None:
        self.verificar_xp_semana.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.guild is not None and await asyncio.to_thread(_xp_ativo, message.guild.id):
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
                            logger.warning(f"Cargo {cargo_id} do nivel {nivel_depois} nao foi encontrado no servidor.")
                        else:
                            try:
                                await message.author.add_roles(cargo)
                            except discord.Forbidden:
                                logger.warning(f"Sem permissao para dar o cargo '{cargo.name}' (nivel {nivel_depois}).")

    @tasks.loop(hours=1)
    async def verificar_xp_semana(self):
        semana_atual = _xp_semana_atual()
        for guild in self.bot.guilds:
            semana_guardada = await asyncio.to_thread(_xp_semana_guardada, guild.id)
            if semana_guardada != semana_atual:
                await asyncio.to_thread(_xp_resetar_semana, guild.id)

    @app_commands.command(name="xp", description="Ve seu rank, o leaderboard do servidor ou configura o sistema de XP")
    @app_commands.guild_only()
    async def xp(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "O que voce quer fazer com o sistema de XP?", view=BotoesXP(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(XP(bot))
