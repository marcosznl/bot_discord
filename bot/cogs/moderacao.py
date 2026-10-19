import asyncio
import datetime
import logging
import os
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot import layout, sqlitestore
from bot.config import DATA_DIR
from bot.permissions import requer_hierarquia, requer_manage_guild, requer_permissao
from bot.retry_ui import LayoutViewComRetry, ModalComRetry, ViewComRetry

logger = logging.getLogger(__name__)

MOD_DB_PATH = os.path.join(DATA_DIR, "moderacao.db")

DURACOES_SILENCIO = [
    app_commands.Choice(name="1 minuto", value=60),
    app_commands.Choice(name="5 minutos", value=5 * 60),
    app_commands.Choice(name="10 minutos", value=10 * 60),
    app_commands.Choice(name="1 hora", value=60 * 60),
    app_commands.Choice(name="1 dia", value=24 * 60 * 60),
    app_commands.Choice(name="1 semana", value=7 * 24 * 60 * 60),
    app_commands.Choice(name="28 dias (maximo)", value=28 * 24 * 60 * 60),
]
APAGAR_MENSAGENS_BAN = [
    app_commands.Choice(name="Nao apagar", value=0),
    app_commands.Choice(name="Ultima hora", value=60 * 60),
    app_commands.Choice(name="Ultimas 24 horas", value=24 * 60 * 60),
    app_commands.Choice(name="Ultimos 7 dias", value=7 * 24 * 60 * 60),
]
MODO_LENTO = [
    app_commands.Choice(name="Desligado", value=0),
    app_commands.Choice(name="5 segundos", value=5),
    app_commands.Choice(name="10 segundos", value=10),
    app_commands.Choice(name="30 segundos", value=30),
    app_commands.Choice(name="1 minuto", value=60),
    app_commands.Choice(name="5 minutos", value=5 * 60),
    app_commands.Choice(name="15 minutos", value=15 * 60),
    app_commands.Choice(name="1 hora", value=60 * 60),
]

_mod_schemas_prontos: set[str] = set()


def _mod_conectar_bd() -> sqlite3.Connection:
    conexao = sqlite3.connect(MOD_DB_PATH)
    if MOD_DB_PATH not in _mod_schemas_prontos:
        conexao.execute(
            "CREATE TABLE IF NOT EXISTS advertencias ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
            "moderador_id INTEGER NOT NULL, motivo TEXT NOT NULL, criado_em REAL NOT NULL)"
        )
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS advertencias_membro ON advertencias (guild_id, user_id)"
        )
        conexao.execute("CREATE TABLE IF NOT EXISTS canal_logs (guild_id INTEGER PRIMARY KEY, canal_id INTEGER NOT NULL)")
        conexao.commit()
        _mod_schemas_prontos.add(MOD_DB_PATH)
    return conexao


def _adv_adicionar(guild_id: int, user_id: int, moderador_id: int, motivo: str) -> int:
    """Registra a advertencia e devolve quantas o membro tem agora."""
    with _mod_conectar_bd() as conexao:
        conexao.execute(
            "INSERT INTO advertencias (guild_id, user_id, moderador_id, motivo, criado_em) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, moderador_id, motivo, time.time()),
        )
        conexao.commit()
        return conexao.execute(
            "SELECT COUNT(*) FROM advertencias WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()[0]


def _adv_listar(guild_id: int, user_id: int) -> list[tuple[int, int, str, float]]:
    return sqlitestore.consultar_todos(
        _mod_conectar_bd,
        "SELECT id, moderador_id, motivo, criado_em FROM advertencias "
        "WHERE guild_id = ? AND user_id = ? ORDER BY criado_em",
        (guild_id, user_id),
    )


def _adv_limpar(guild_id: int, user_id: int) -> int:
    return sqlitestore.executar_retornando_rowcount(
        _mod_conectar_bd, "DELETE FROM advertencias WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )


def _logs_canal(guild_id: int) -> int | None:
    linha = sqlitestore.consultar_um(_mod_conectar_bd, "SELECT canal_id FROM canal_logs WHERE guild_id = ?", (guild_id,))
    return linha[0] if linha else None


def _logs_definir(guild_id: int, canal_id: int | None) -> None:
    if canal_id is None:
        sqlitestore.executar(_mod_conectar_bd, "DELETE FROM canal_logs WHERE guild_id = ?", (guild_id,))
    else:
        sqlitestore.executar(
            _mod_conectar_bd,
            "INSERT INTO canal_logs (guild_id, canal_id) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET canal_id = excluded.canal_id",
            (guild_id, canal_id),
        )


def _motivo_auditoria(interaction: discord.Interaction, motivo: str | None) -> str:
    # Aparece no Registro de Auditoria do servidor - sem o nome de quem usou
    # o comando, todas as acoes ficariam registradas como feitas pelo bot.
    return f"{motivo or 'Sem motivo informado'} (por {interaction.user} via bot)"


def _cortar(texto: str, limite: int = 1000) -> str:
    return texto if len(texto) <= limite else texto[: limite - 3] + "..."


async def enviar_log(guild: discord.Guild, titulo: str, descricao: str, cor: discord.Color) -> bool:
    """Manda um aviso no canal de logs do servidor, se houver um configurado.
    Devolve False se nao ha canal (ou o bot nao consegue escrever nele)."""
    canal_id = await asyncio.to_thread(_logs_canal, guild.id)
    canal = guild.get_channel(canal_id) if canal_id else None
    if canal is None:
        return False
    embed = discord.Embed(title=titulo, description=descricao, color=cor, timestamp=discord.utils.utcnow())
    try:
        await canal.send(embed=embed)
    except discord.HTTPException as erro:
        logger.warning(f"Nao consegui escrever no canal de logs de {guild.id}: {erro}")
        return False
    return True


async def _avisar_por_dm(usuario: discord.abc.User, texto: str) -> None:
    # Muita gente bloqueia DM de membros do servidor - nao e erro.
    try:
        await usuario.send(texto)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------- /logs


class BotoesLogs(ViewComRetry):
    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.select_canal = discord.ui.ChannelSelect(
            placeholder="Escolha o canal de logs",
            channel_types=[discord.ChannelType.text],
        )
        self.select_canal.callback = self._canal_escolhido
        self.add_item(self.select_canal)

    async def _canal_escolhido(self, interaction: discord.Interaction):
        canal = self.select_canal.values[0]
        await asyncio.to_thread(_logs_definir, self.guild_id, canal.id)
        await interaction.response.send_message(
            f"Logs vao para {canal.mention}. Deixe esse canal visivel so para a moderacao.", ephemeral=True
        )

    @discord.ui.button(label="Ver configuracao", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, botao: discord.ui.Button):
        canal_id = await asyncio.to_thread(_logs_canal, self.guild_id)
        if canal_id is None:
            await interaction.response.send_message("Logs desativados (nenhum canal configurado).", ephemeral=True)
            return
        await interaction.response.send_message(f"Logs ativos em <#{canal_id}>.", ephemeral=True)

    @discord.ui.button(label="Desativar", style=discord.ButtonStyle.danger)
    async def desativar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await asyncio.to_thread(_logs_definir, self.guild_id, None)
        await interaction.response.send_message("Logs desativados.", ephemeral=True)


# ---------------------------------------------------------------- /automod

PREFIXO_REGRA = "[Bot] "
REGRA_PALAVROES = PREFIXO_REGRA + "Palavroes e ofensas"
REGRA_SPAM = PREFIXO_REGRA + "Spam"
REGRA_MENCOES = PREFIXO_REGRA + "Spam de mencoes"
REGRA_PALAVRAS = PREFIXO_REGRA + "Palavras proibidas"
LIMITE_MENCOES = 5


def _gatilho_regra(nome: str, palavras: list[str] | None = None) -> discord.AutoModTrigger:
    if nome == REGRA_PALAVROES:
        return discord.AutoModTrigger(presets=discord.AutoModPresets.all())
    if nome == REGRA_SPAM:
        return discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam)
    if nome == REGRA_MENCOES:
        return discord.AutoModTrigger(mention_limit=LIMITE_MENCOES, mention_raid_protection=True)
    return discord.AutoModTrigger(keyword_filter=palavras or [])


def _acoes_regra(nome: str, canal_logs_id: int | None) -> list[discord.AutoModRuleAction]:
    if nome == REGRA_SPAM:
        # Regra de spam nao aceita mensagem personalizada no bloqueio.
        acoes = [discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)]
    else:
        acoes = [discord.AutoModRuleAction(custom_message="Sua mensagem foi bloqueada pela moderacao automatica.")]
    if canal_logs_id:
        acoes.append(discord.AutoModRuleAction(channel_id=canal_logs_id))
    return acoes


def _palavras_de_texto(texto: str) -> list[str]:
    """'a, b,  c' -> ['a', 'b', 'c'] (sem vazios/duplicadas, max 60 letras cada)."""
    vistas: list[str] = []
    for parte in texto.replace("\n", ",").split(","):
        palavra = parte.strip().lower()[:60]
        if palavra and palavra not in vistas:
            vistas.append(palavra)
    return vistas


async def _regras_do_bot(guild: discord.Guild) -> dict[str, discord.AutoModRule]:
    regras = await guild.fetch_automod_rules()
    return {regra.name: regra for regra in regras if regra.name.startswith(PREFIXO_REGRA)}


async def _alternar_regra(bot: commands.Bot, guild: discord.Guild, nome: str) -> None:
    regras = await _regras_do_bot(guild)
    regra = regras.get(nome)
    if regra is not None:
        await regra.delete(reason="Desativada via /automod")
        return
    canal_logs_id = await asyncio.to_thread(_logs_canal, guild.id)
    await guild.create_automod_rule(
        name=nome,
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=_gatilho_regra(nome),
        actions=_acoes_regra(nome, canal_logs_id),
        enabled=True,
        reason="Ativada via /automod",
    )


async def _salvar_palavras(guild: discord.Guild, palavras: list[str]) -> None:
    regras = await _regras_do_bot(guild)
    regra = regras.get(REGRA_PALAVRAS)
    if not palavras:
        if regra is not None:
            await regra.delete(reason="Lista de palavras proibidas esvaziada via /automod")
        return
    if regra is not None:
        await regra.edit(trigger=_gatilho_regra(REGRA_PALAVRAS, palavras), reason="Editada via /automod")
        return
    canal_logs_id = await asyncio.to_thread(_logs_canal, guild.id)
    await guild.create_automod_rule(
        name=REGRA_PALAVRAS,
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=_gatilho_regra(REGRA_PALAVRAS, palavras),
        actions=_acoes_regra(REGRA_PALAVRAS, canal_logs_id),
        enabled=True,
        reason="Criada via /automod",
    )


DESCRICOES_REGRAS = {
    REGRA_PALAVROES: "Bloqueia palavroes, conteudo sexual e ofensas (listas prontas do Discord).",
    REGRA_SPAM: "Bloqueia mensagens que o Discord identifica como spam.",
    REGRA_MENCOES: f"Bloqueia mensagens com mais de {LIMITE_MENCOES} mencoes e protege contra raids de mencao.",
}


class ModalPalavrasProibidas(ModalComRetry, title="Palavras proibidas"):
    def __init__(self, painel: "PainelAutoMod", atuais: list[str]):
        super().__init__()
        self.painel = painel
        self.palavras = discord.ui.TextInput(
            label="Separadas por virgula (vazio desativa)",
            style=discord.TextStyle.paragraph,
            default=", ".join(atuais),
            required=False,
            max_length=4000,
        )
        self.add_item(self.palavras)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await _salvar_palavras(interaction.guild, _palavras_de_texto(str(self.palavras)))
        await self.painel.atualizar(interaction)


class PainelAutoMod(LayoutViewComRetry):
    def __init__(self, bot: commands.Bot, regras: dict[str, discord.AutoModRule]):
        super().__init__(timeout=600)
        self.bot = bot
        self.regras = regras

        container = discord.ui.Container(
            layout.texto(
                "## Moderacao automatica\n"
                "Regras nativas do Discord: a mensagem e bloqueada antes de aparecer no chat. "
                "Se houver canal de `/logs`, cada bloqueio gera um alerta la."
            ),
            accent_color=discord.Color.red(),
        )
        for nome, descricao in DESCRICOES_REGRAS.items():
            ativa = nome in regras
            container.add_item(layout.separador())
            container.add_item(
                discord.ui.Section(
                    f"**{nome.removeprefix(PREFIXO_REGRA)}** - {'ativa' if ativa else 'desligada'}\n{descricao}",
                    accessory=layout.botao(
                        "Desligar" if ativa else "Ligar",
                        self._alternador(nome),
                        discord.ButtonStyle.secondary if ativa else discord.ButtonStyle.success,
                    ),
                )
            )

        regra_palavras = regras.get(REGRA_PALAVRAS)
        palavras = regra_palavras.trigger.keyword_filter if regra_palavras else []
        resumo = ", ".join(palavras[:15]) + (" ..." if len(palavras) > 15 else "") if palavras else "nenhuma"
        container.add_item(layout.separador())
        container.add_item(
            discord.ui.Section(
                f"**Palavras proibidas** - {len(palavras)} configurada(s)\n-# {resumo}",
                accessory=layout.botao("Editar", self._editar_palavras, discord.ButtonStyle.primary),
            )
        )
        self.add_item(container)

    def _alternador(self, nome: str):
        async def alternar(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await _alternar_regra(self.bot, interaction.guild, nome)
            await self.atualizar(interaction)

        return alternar

    async def _editar_palavras(self, interaction: discord.Interaction) -> None:
        regra = self.regras.get(REGRA_PALAVRAS)
        atuais = regra.trigger.keyword_filter if regra else []
        await interaction.response.send_modal(ModalPalavrasProibidas(self, atuais))

    async def atualizar(self, interaction: discord.Interaction) -> None:
        regras = await _regras_do_bot(interaction.guild)
        self.stop()
        await interaction.edit_original_response(view=PainelAutoMod(self.bot, regras))


# ---------------------------------------------------------------- denuncia


class ModalDenuncia(ModalComRetry, title="Denunciar mensagem"):
    motivo = discord.ui.TextInput(
        label="O que tem de errado nessa mensagem?", style=discord.TextStyle.paragraph, max_length=500
    )

    def __init__(self, bot: commands.Bot, mensagem: discord.Message):
        super().__init__()
        self.bot = bot
        self.mensagem = mensagem

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mensagem = self.mensagem
        conteudo = _cortar(mensagem.content) if mensagem.content else "(sem texto)"
        enviado = await enviar_log(
            interaction.guild,
            "Mensagem denunciada",
            f"**Denunciante:** {interaction.user.mention}\n"
            f"**Autor:** {mensagem.author.mention}\n"
            f"**Canal:** {mensagem.channel.mention} - [ir para a mensagem]({mensagem.jump_url})\n"
            f"**Motivo:** {self.motivo}\n\n**Conteudo:**\n{conteudo}",
            discord.Color.orange(),
        )
        if enviado:
            await interaction.followup.send("Denuncia enviada para a moderacao. Obrigado!", ephemeral=True)
        else:
            await interaction.followup.send(
                "Este servidor nao tem canal de logs configurado, entao a denuncia nao tem para onde ir. "
                "Avise um moderador diretamente.",
                ephemeral=True,
            )


# ---------------------------------------------------------------- cog


class Moderacao(commands.Cog):
    mod = app_commands.Group(
        name="mod",
        description="Ferramentas de moderacao",
        guild_only=True,
        default_permissions=discord.Permissions(moderate_members=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.menu_denunciar = app_commands.ContextMenu(name="Denunciar mensagem", callback=self.denunciar)
        self.menu_fixar = app_commands.ContextMenu(name="Fixar ou desafixar", callback=self.fixar)
        self.bot.tree.add_command(self.menu_denunciar)
        self.bot.tree.add_command(self.menu_fixar)

    async def cog_unload(self) -> None:
        for menu in (self.menu_denunciar, self.menu_fixar):
            self.bot.tree.remove_command(menu.name, type=menu.type)

    # ---- menus de clique direito

    @app_commands.guild_only()
    async def denunciar(self, interaction: discord.Interaction, mensagem: discord.Message):
        if mensagem.author.id == interaction.user.id:
            await interaction.response.send_message("Voce nao pode denunciar a propria mensagem.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalDenuncia(self.bot, mensagem))

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def fixar(self, interaction: discord.Interaction, mensagem: discord.Message):
        if not await requer_permissao(interaction, "manage_messages", "esse menu"):
            return
        motivo = f"Por {interaction.user} via bot"
        if mensagem.pinned:
            await mensagem.unpin(reason=motivo)
            await interaction.response.send_message("Mensagem desafixada.", ephemeral=True)
        else:
            await mensagem.pin(reason=motivo)
            await interaction.response.send_message("Mensagem fixada.", ephemeral=True)

    # ---- /mod

    @mod.command(name="silenciar", description="Silencia um membro por um tempo (castigo do Discord)")
    @app_commands.describe(membro="Quem silenciar", duracao="Por quanto tempo", motivo="Motivo (aparece na auditoria)")
    @app_commands.choices(duracao=DURACOES_SILENCIO)
    async def silenciar(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        duracao: app_commands.Choice[int],
        motivo: str | None = None,
    ):
        if not await requer_permissao(interaction, "moderate_members", "esse comando"):
            return
        if not await requer_hierarquia(interaction, membro, "silenciar"):
            return
        await membro.timeout(datetime.timedelta(seconds=duracao.value), reason=_motivo_auditoria(interaction, motivo))
        await interaction.response.send_message(
            f"{membro.mention} silenciado(a) por {duracao.name}." + (f" Motivo: {motivo}" if motivo else "")
        )

    @mod.command(name="dessilenciar", description="Tira o silencio (castigo) de um membro")
    @app_commands.describe(membro="Quem dessilenciar")
    async def dessilenciar(self, interaction: discord.Interaction, membro: discord.Member):
        if not await requer_permissao(interaction, "moderate_members", "esse comando"):
            return
        if not membro.is_timed_out():
            await interaction.response.send_message(f"{membro.mention} nao esta silenciado(a).", ephemeral=True)
            return
        await membro.timeout(None, reason=_motivo_auditoria(interaction, "Silencio removido"))
        await interaction.response.send_message(f"{membro.mention} pode falar de novo.")

    @mod.command(name="expulsar", description="Expulsa um membro do servidor (ele pode voltar com convite)")
    @app_commands.describe(membro="Quem expulsar", motivo="Motivo (enviado por DM e na auditoria)")
    async def expulsar(self, interaction: discord.Interaction, membro: discord.Member, motivo: str | None = None):
        if not await requer_permissao(interaction, "kick_members", "esse comando"):
            return
        if not await requer_hierarquia(interaction, membro, "expulsar"):
            return
        await interaction.response.defer()
        await _avisar_por_dm(
            membro, f"Voce foi expulso(a) de **{interaction.guild.name}**." + (f" Motivo: {motivo}" if motivo else "")
        )
        await membro.kick(reason=_motivo_auditoria(interaction, motivo))
        await interaction.followup.send(f"{membro} foi expulso(a)." + (f" Motivo: {motivo}" if motivo else ""))

    @mod.command(name="banir", description="Bane alguem do servidor (funciona ate com quem ja saiu, pelo ID)")
    @app_commands.describe(
        usuario="Quem banir (membro ou ID)",
        apagar_mensagens="Apagar as mensagens recentes dessa pessoa?",
        motivo="Motivo (enviado por DM e na auditoria)",
    )
    @app_commands.choices(apagar_mensagens=APAGAR_MENSAGENS_BAN)
    async def banir(
        self,
        interaction: discord.Interaction,
        usuario: discord.User,
        apagar_mensagens: app_commands.Choice[int] | None = None,
        motivo: str | None = None,
    ):
        if not await requer_permissao(interaction, "ban_members", "esse comando"):
            return
        membro = interaction.guild.get_member(usuario.id)
        if isinstance(usuario, discord.Member):
            membro = usuario
        if membro is not None and not await requer_hierarquia(interaction, membro, "banir"):
            return
        await interaction.response.defer()
        if membro is not None:
            await _avisar_por_dm(
                membro, f"Voce foi banido(a) de **{interaction.guild.name}**." + (f" Motivo: {motivo}" if motivo else "")
            )
        await interaction.guild.ban(
            usuario,
            delete_message_seconds=apagar_mensagens.value if apagar_mensagens else 0,
            reason=_motivo_auditoria(interaction, motivo),
        )
        await interaction.followup.send(f"{usuario} foi banido(a)." + (f" Motivo: {motivo}" if motivo else ""))

    @mod.command(name="desbanir", description="Remove o banimento de alguem")
    @app_commands.describe(usuario="Comece a digitar o nome - a lista vem dos banidos do servidor")
    async def desbanir(self, interaction: discord.Interaction, usuario: str):
        if not await requer_permissao(interaction, "ban_members", "esse comando"):
            return
        if not usuario.isdigit():
            await interaction.response.send_message("Escolha alguem da lista (ou cole o ID).", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await interaction.guild.unban(discord.Object(int(usuario)), reason=_motivo_auditoria(interaction, None))
        except discord.NotFound:
            await interaction.followup.send("Essa pessoa nao esta banida.")
            return
        await interaction.followup.send(f"<@{usuario}> foi desbanido(a).", allowed_mentions=discord.AllowedMentions.none())

    @desbanir.autocomplete("usuario")
    async def _desbanir_autocomplete(self, interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[str]]:
        if interaction.guild is None or not interaction.app_permissions.ban_members:
            return []
        atual = atual.lower()
        opcoes = []
        async for ban in interaction.guild.bans(limit=1000):
            if atual in ban.user.name.lower() or atual in str(ban.user.id):
                opcoes.append(app_commands.Choice(name=f"{ban.user.name} ({ban.user.id})"[:100], value=str(ban.user.id)))
                if len(opcoes) == 25:
                    break
        return opcoes

    @mod.command(name="advertir", description="Registra uma advertencia para um membro (ele recebe por DM)")
    @app_commands.describe(membro="Quem advertir", motivo="Motivo da advertencia")
    async def advertir(
        self, interaction: discord.Interaction, membro: discord.Member, motivo: app_commands.Range[str, 1, 500]
    ):
        if not await requer_permissao(interaction, "moderate_members", "esse comando"):
            return
        if membro.bot:
            await interaction.response.send_message("Bots nao recebem advertencia.", ephemeral=True)
            return
        await interaction.response.defer()
        total = await asyncio.to_thread(_adv_adicionar, interaction.guild.id, membro.id, interaction.user.id, motivo)
        await _avisar_por_dm(
            membro, f"Voce recebeu uma advertencia em **{interaction.guild.name}**: {motivo}\nTotal: {total}."
        )
        await enviar_log(
            interaction.guild,
            "Advertencia",
            f"**Membro:** {membro.mention}\n**Moderador:** {interaction.user.mention}\n"
            f"**Motivo:** {motivo}\n**Total de advertencias:** {total}",
            discord.Color.yellow(),
        )
        await interaction.followup.send(f"{membro.mention} advertido(a) ({total} no total). Motivo: {motivo}")

    @mod.command(name="advertencias", description="Lista as advertencias de um membro")
    @app_commands.describe(membro="De quem ver as advertencias")
    async def advertencias(self, interaction: discord.Interaction, membro: discord.Member):
        if not await requer_permissao(interaction, "moderate_members", "esse comando"):
            return
        lista = await asyncio.to_thread(_adv_listar, interaction.guild.id, membro.id)
        if not lista:
            await interaction.response.send_message(f"{membro.mention} nao tem advertencias.", ephemeral=True)
            return
        linhas = [
            f"**{posicao}.** {discord.utils.format_dt(datetime.datetime.fromtimestamp(criado_em), 'd')} "
            f"por <@{moderador_id}>: {motivo}"
            for posicao, (_, moderador_id, motivo, criado_em) in enumerate(lista[-20:], start=max(1, len(lista) - 19))
        ]
        view = layout.view_estatica(
            layout.secao_com_miniatura(
                f"## Advertencias de {membro.display_name}\nTotal: {len(lista)}", membro.display_avatar.url
            ),
            layout.separador(),
            layout.texto(_cortar("\n".join(linhas), 3500)),
            cor=discord.Color.yellow(),
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @mod.command(name="perdoar", description="Apaga todas as advertencias de um membro")
    @app_commands.describe(membro="De quem apagar as advertencias")
    async def perdoar(self, interaction: discord.Interaction, membro: discord.Member):
        if not await requer_permissao(interaction, "moderate_members", "esse comando"):
            return
        removidas = await asyncio.to_thread(_adv_limpar, interaction.guild.id, membro.id)
        await interaction.response.send_message(
            f"{removidas} advertencia(s) de {membro.mention} apagada(s).", ephemeral=True
        )

    @mod.command(name="limpar", description="Apaga mensagens recentes deste canal")
    @app_commands.describe(quantidade="Quantas mensagens olhar (1 a 200)", membro="Apagar so as mensagens dessa pessoa")
    async def limpar(
        self,
        interaction: discord.Interaction,
        quantidade: app_commands.Range[int, 1, 200],
        membro: discord.Member | None = None,
    ):
        if not await requer_permissao(interaction, "manage_messages", "esse comando"):
            return
        await interaction.response.defer(ephemeral=True)
        filtro = (lambda m: m.author.id == membro.id) if membro else (lambda m: True)
        apagadas = await interaction.channel.purge(limit=quantidade, check=filtro, reason=_motivo_auditoria(interaction, None))
        await interaction.followup.send(f"{len(apagadas)} mensagem(ns) apagada(s).", ephemeral=True)

    @mod.command(name="lento", description="Liga ou desliga o modo lento deste canal")
    @app_commands.describe(intervalo="Tempo minimo entre mensagens de cada pessoa")
    @app_commands.choices(intervalo=MODO_LENTO)
    async def lento(self, interaction: discord.Interaction, intervalo: app_commands.Choice[int]):
        if not await requer_permissao(interaction, "manage_channels", "esse comando"):
            return
        canal = interaction.channel
        if not isinstance(canal, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            await interaction.response.send_message("Esse tipo de canal nao tem modo lento.", ephemeral=True)
            return
        await canal.edit(slowmode_delay=intervalo.value, reason=_motivo_auditoria(interaction, None))
        if intervalo.value:
            await interaction.response.send_message(f"Modo lento ligado: {intervalo.name} entre mensagens.")
        else:
            await interaction.response.send_message("Modo lento desligado.")

    @mod.command(name="trancar", description="Impede todo mundo (sem cargo especial) de falar neste canal")
    @app_commands.describe(motivo="Motivo (aparece no canal)")
    async def trancar(self, interaction: discord.Interaction, motivo: str | None = None):
        await self._trancar_destrancar(interaction, trancar=True, motivo=motivo)

    @mod.command(name="destrancar", description="Libera de novo o canal trancado")
    async def destrancar(self, interaction: discord.Interaction):
        await self._trancar_destrancar(interaction, trancar=False, motivo=None)

    async def _trancar_destrancar(self, interaction: discord.Interaction, trancar: bool, motivo: str | None) -> None:
        if not await requer_permissao(interaction, "manage_channels", "esse comando"):
            return
        canal = interaction.channel
        if not isinstance(canal, discord.TextChannel):
            await interaction.response.send_message("So da pra trancar canais de texto.", ephemeral=True)
            return
        cargo_todos = interaction.guild.default_role
        permissoes = canal.overwrites_for(cargo_todos)
        # None (e nao True) ao destrancar: volta a herdar da categoria em vez
        # de liberar explicitamente algo que talvez nunca tenha sido liberado.
        permissoes.send_messages = False if trancar else None
        permissoes.send_messages_in_threads = False if trancar else None
        permissoes.create_public_threads = False if trancar else None
        await canal.set_permissions(cargo_todos, overwrite=permissoes, reason=_motivo_auditoria(interaction, motivo))
        if trancar:
            await interaction.response.send_message("🔒 Canal trancado." + (f" Motivo: {motivo}" if motivo else ""))
        else:
            await interaction.response.send_message("🔓 Canal destrancado.")

    # ---- /logs e /automod

    @app_commands.command(name="logs", description="Configura o canal de logs da moderacao")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def logs(self, interaction: discord.Interaction):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        await interaction.response.send_message(
            "Escolha o canal que vai receber os logs (mensagens apagadas/editadas, entradas, saidas, "
            "banimentos, castigos, advertencias, denuncias e alertas do AutoMod):",
            view=BotoesLogs(interaction.guild.id),
            ephemeral=True,
        )

    @app_commands.command(name="automod", description="Liga/desliga as regras de moderacao automatica do Discord")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def automod(self, interaction: discord.Interaction):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        if not interaction.app_permissions.manage_guild:
            await interaction.response.send_message(
                "Eu preciso da permissao Gerenciar Servidor para mexer no AutoMod.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        regras = await _regras_do_bot(interaction.guild)
        await interaction.followup.send(view=PainelAutoMod(self.bot, regras), ephemeral=True)

    # ---- eventos que vao para o canal de logs

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        mensagem = payload.cached_message
        if guild is None or (mensagem is not None and mensagem.author.bot):
            return
        if mensagem is None:
            descricao = f"**Canal:** <#{payload.channel_id}>\n-# Mensagem antiga - conteudo fora do cache do bot."
        else:
            anexos = "\n".join(anexo.filename for anexo in mensagem.attachments)
            descricao = (
                f"**Autor:** {mensagem.author.mention}\n**Canal:** <#{payload.channel_id}>\n\n"
                f"{_cortar(mensagem.content) or '(sem texto)'}" + (f"\n\n**Anexos:**\n{anexos}" if anexos else "")
            )
        await enviar_log(guild, "Mensagem apagada", descricao, discord.Color.red())

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if payload.guild_id is None:
            return
        depois = payload.message
        antes = payload.cached_message
        if depois.author.bot or (antes is not None and antes.content == depois.content):
            # Edicoes sem mudanca de texto sao o Discord carregando preview de link.
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        texto_antes = _cortar(antes.content, 900) if antes is not None else "(fora do cache do bot)"
        await enviar_log(
            guild,
            "Mensagem editada",
            f"**Autor:** {depois.author.mention}\n**Canal:** <#{payload.channel_id}> - [ir]({depois.jump_url})\n\n"
            f"**Antes:**\n{texto_antes or '(sem texto)'}\n\n**Depois:**\n{_cortar(depois.content, 900) or '(sem texto)'}",
            discord.Color.blurple(),
        )

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        criada = discord.utils.format_dt(membro.created_at, "R")
        await enviar_log(
            membro.guild, "Entrou", f"{membro.mention} ({membro})\nConta criada {criada}", discord.Color.green()
        )

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        await enviar_log(membro.guild, "Saiu", f"{membro.mention} ({membro})", discord.Color.dark_grey())

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entrada: discord.AuditLogEntry):
        # Pega acoes de moderacao feitas por QUALQUER caminho (comandos do
        # bot, menu do Discord, outros bots) ja com quem fez e o motivo.
        # Precisa da permissao Ver Registro de Auditoria no bot.
        acao = entrada.action
        alvo = f"<@{entrada.target.id}>" if entrada.target is not None else "?"
        autor = f"<@{entrada.user_id}>" if entrada.user_id else "?"
        motivo = entrada.reason or "Sem motivo informado"

        if acao is discord.AuditLogAction.ban:
            titulo, cor = "Banido", discord.Color.dark_red()
        elif acao is discord.AuditLogAction.unban:
            titulo, cor = "Desbanido", discord.Color.green()
        elif acao is discord.AuditLogAction.kick:
            titulo, cor = "Expulso", discord.Color.orange()
        elif acao is discord.AuditLogAction.member_update and hasattr(entrada.after, "timed_out_until"):
            ate = entrada.after.timed_out_until
            if ate is None:
                titulo, cor = "Silencio removido", discord.Color.green()
            else:
                titulo, cor = "Silenciado", discord.Color.orange()
                motivo += f"\n**Ate:** {discord.utils.format_dt(ate, 'f')}"
        else:
            return

        await enviar_log(
            entrada.guild, titulo, f"**Membro:** {alvo}\n**Por:** {autor}\n**Motivo:** {motivo}", cor
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderacao(bot))
