import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot import banco, configstore, layout
from bot.permissions import cargo_perigoso, motivo_cargo_invalido, requer_permissao
from bot.retry_ui import ModalDoBot

logger = logging.getLogger(__name__)

# {"canal": id onde chegam as candidaturas, "areas": [str], "cargo": id ou None}
CHAVE_CONFIG = "candidaturas"
DISPONIBILIDADES = ["Manha", "Tarde", "Noite", "Fins de semana"]
TAMANHO_MAX_ANEXO = 8 * 1024 * 1024

banco.registrar_schema(
    """
    CREATE TABLE IF NOT EXISTS candidaturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        area TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pendente',
        criado_em REAL NOT NULL
    );
    """
)


def candidatura_pendente(guild_id: int, user_id: int) -> bool:
    return banco.um(
        "SELECT 1 FROM candidaturas WHERE guild_id = ? AND user_id = ? AND status = 'pendente'", (guild_id, user_id)
    ) is not None


def candidatura_criar(guild_id: int, user_id: int, area: str) -> int:
    with banco.conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO candidaturas (guild_id, user_id, area, criado_em) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, area, time.time()),
        )
        conexao.commit()
        return cursor.lastrowid


def candidatura_obter(candidatura_id: int) -> tuple[int, int, str, str] | None:
    return banco.um("SELECT guild_id, user_id, area, status FROM candidaturas WHERE id = ?", (candidatura_id,))


def candidatura_definir_status(candidatura_id: int, status: str) -> bool:
    """Muda o status so se ainda estiver pendente (evita dois moderadores
    decidindo a mesma candidatura ao mesmo tempo)."""
    return banco.executar_rowcount(
        "UPDATE candidaturas SET status = ? WHERE id = ? AND status = 'pendente'", (status, candidatura_id)
    ) > 0


class ModalCandidatura(ModalDoBot, title="Candidatura para a equipe"):
    def __init__(self, areas: list[str]):
        super().__init__(timeout=900)
        self.area = discord.ui.Select(
            placeholder="Escolha uma area",
            options=[discord.SelectOption(label=area[:100], value=area[:100]) for area in areas[:25]],
        )
        self.motivo = discord.ui.TextInput(style=discord.TextStyle.paragraph, min_length=30, max_length=1000)
        self.disponibilidade = discord.ui.CheckboxGroup(
            options=[discord.CheckboxGroupOption(label=opcao) for opcao in DISPONIBILIDADES],
            min_values=1,
            max_values=len(DISPONIBILIDADES),
        )
        self.anexos = discord.ui.FileUpload(required=False, max_values=3)
        self.regras = discord.ui.Checkbox()

        self.add_item(discord.ui.Label(text="Area", component=self.area))
        self.add_item(
            discord.ui.Label(
                text="Por que voce quer entrar?",
                description="Conte sua experiencia e o que pretende fazer",
                component=self.motivo,
            )
        )
        self.add_item(discord.ui.Label(text="Quando voce costuma estar online?", component=self.disponibilidade))
        self.add_item(
            discord.ui.Label(
                text="Prints ou portfolio (opcional)",
                description="Ate 3 arquivos",
                component=self.anexos,
            )
        )
        self.add_item(discord.ui.Label(text="Li e aceito as regras do servidor", component=self.regras))

    async def on_submit(self, interaction: discord.Interaction):
        if not self.regras.value:
            await interaction.response.send_message("Voce precisa aceitar as regras para se candidatar.", ephemeral=True)
            return
        config = await asyncio.to_thread(configstore.obter, interaction.guild.id, CHAVE_CONFIG)
        canal = interaction.guild.get_channel((config or {}).get("canal") or 0)
        if canal is None:
            await interaction.response.send_message("As candidaturas foram desativadas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        area = self.area.values[0]
        candidatura_id = await asyncio.to_thread(candidatura_criar, interaction.guild.id, interaction.user.id, area)

        # Os anexos de um modal tem link temporario - reenvia os arquivos
        # junto da candidatura para nao sumirem depois.
        arquivos, links = [], []
        for anexo in self.anexos.values:
            if anexo.size <= TAMANHO_MAX_ANEXO:
                arquivos.append(await anexo.to_file())
            else:
                links.append(anexo.url)

        membro = interaction.user
        itens: list[discord.ui.Item] = [
            layout.secao_com_miniatura(
                f"## Candidatura #{candidatura_id}\n"
                f"**Quem:** {membro.mention} ({membro})\n"
                f"**Conta criada:** {discord.utils.format_dt(membro.created_at, 'R')}\n"
                f"**Area:** {area}\n"
                f"**Disponibilidade:** {', '.join(self.disponibilidade.values)}",
                membro.display_avatar.url,
            ),
            layout.separador(),
            layout.texto(f"**Por que quer entrar:**\n{self.motivo.value}"),
        ]
        imagens = [arquivo for arquivo in arquivos if arquivo.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]
        outros = [arquivo for arquivo in arquivos if arquivo not in imagens]
        if imagens:
            itens.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(f"attachment://{a.filename}") for a in imagens)))
        for arquivo in outros:
            itens.append(discord.ui.File(f"attachment://{arquivo.filename}"))
        if links:
            itens.append(layout.texto("**Arquivos grandes:**\n" + "\n".join(links)))
        itens.append(
            layout.linha(BotaoDecisao(candidatura_id, "aprovar"), BotaoDecisao(candidatura_id, "recusar"))
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(*itens, accent_color=discord.Color.blurple()))
        await canal.send(view=view, files=arquivos, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(
            "Candidatura enviada! Voce recebe a resposta por DM.", ephemeral=True
        )


class BotaoCandidatar(discord.ui.DynamicItem[discord.ui.Button], template=r"candidatura:abrir"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Quero me candidatar", emoji="📝", style=discord.ButtonStyle.primary, custom_id="candidatura:abrir")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        config = await asyncio.to_thread(configstore.obter, interaction.guild.id, CHAVE_CONFIG)
        if not config or interaction.guild.get_channel(config.get("canal") or 0) is None:
            await interaction.response.send_message("As candidaturas estao fechadas no momento.", ephemeral=True)
            return
        if await asyncio.to_thread(candidatura_pendente, interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Voce ja tem uma candidatura em analise. Aguarde a resposta.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ModalCandidatura(config["areas"]))


class BotaoDecisao(discord.ui.DynamicItem[discord.ui.Button], template=r"candidatura:(?P<id>\d+):(?P<acao>aprovar|recusar)"):
    def __init__(self, candidatura_id: int, acao: str):
        estilo = discord.ButtonStyle.success if acao == "aprovar" else discord.ButtonStyle.danger
        super().__init__(
            discord.ui.Button(label=acao.capitalize(), style=estilo, custom_id=f"candidatura:{candidatura_id}:{acao}")
        )
        self.candidatura_id = candidatura_id
        self.acao = acao

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["id"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("So quem tem Gerenciar Servidor pode decidir.", ephemeral=True)
            return
        status = "aprovada" if self.acao == "aprovar" else "recusada"
        if not await asyncio.to_thread(candidatura_definir_status, self.candidatura_id, status):
            await interaction.response.send_message("Essa candidatura ja foi decidida.", ephemeral=True)
            return

        # Troca os botoes pelo resultado, mantendo o resto do cartao.
        view = discord.ui.LayoutView.from_message(interaction.message, timeout=None)
        for linha in [item for item in view.walk_children() if isinstance(item, discord.ui.ActionRow)]:
            (linha.parent or view).remove_item(linha)
        container = next((item for item in view.children if isinstance(item, discord.ui.Container)), None)
        if container is not None:
            emoji = "✅" if status == "aprovada" else "❌"
            container.add_item(layout.texto(f"{emoji} **{status.capitalize()}** por {interaction.user.mention}"))
            container.accent_color = discord.Color.green() if status == "aprovada" else discord.Color.red()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())

        guild_id, user_id, area, _ = await asyncio.to_thread(candidatura_obter, self.candidatura_id)
        membro = interaction.guild.get_member(user_id)
        if membro is None:
            try:
                membro = await interaction.guild.fetch_member(user_id)
            except discord.NotFound:
                return
        if status == "aprovada":
            config = await asyncio.to_thread(configstore.obter, guild_id, CHAVE_CONFIG) or {}
            cargo = interaction.guild.get_role(config.get("cargo") or 0)
            if cargo is not None and cargo < interaction.guild.me.top_role and not cargo_perigoso(cargo):
                await membro.add_roles(cargo, reason=f"Candidatura #{self.candidatura_id} aprovada")
            texto = f"✅ Sua candidatura para **{area}** em **{interaction.guild.name}** foi aprovada! Bem-vindo(a) a equipe."
        else:
            texto = f"Sua candidatura para **{area}** em **{interaction.guild.name}** nao foi aprovada desta vez. Obrigado pelo interesse!"
        try:
            await membro.send(texto)
        except discord.HTTPException:
            pass


class Candidaturas(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="candidaturas", description="Publica um painel para membros se candidatarem a equipe")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        canal_respostas="Canal privado onde as candidaturas vao chegar",
        areas="Areas separadas por ponto e virgula (ex: Moderacao; Eventos; Design)",
        cargo_aprovado="Cargo dado automaticamente a quem for aprovado (opcional)",
        mensagem="Texto do painel (opcional)",
    )
    async def candidaturas(
        self,
        interaction: discord.Interaction,
        canal_respostas: discord.TextChannel,
        areas: str,
        cargo_aprovado: discord.Role | None = None,
        mensagem: app_commands.Range[str, 1, 1000] | None = None,
    ):
        if not await requer_permissao(interaction, "manage_guild", "esse comando"):
            return
        lista = [area.strip()[:100] for area in areas.split(";") if area.strip()][:25]
        if not lista:
            await interaction.response.send_message("Informe pelo menos uma area.", ephemeral=True)
            return
        if cargo_aprovado is not None:
            motivo = motivo_cargo_invalido(cargo_aprovado, interaction.user)
            if motivo:
                await interaction.response.send_message(
                    f"Nao da para dar {cargo_aprovado.mention} automaticamente: {motivo}. "
                    "Deixe sem cargo e de o cargo a mao depois.",
                    ephemeral=True,
                )
                return

        await asyncio.to_thread(
            configstore.definir,
            interaction.guild.id,
            CHAVE_CONFIG,
            {"canal": canal_respostas.id, "areas": lista, "cargo": cargo_aprovado.id if cargo_aprovado else None},
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "## Faca parte da equipe\n"
                    + (mensagem or "Quer ajudar o servidor? Clique no botao e preencha o formulario.")
                    + f"\n-# Areas: {', '.join(lista)}",
                    accessory=BotaoCandidatar(),
                ),
                accent_color=discord.Color.blurple(),
            )
        )
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(view=view)
        await interaction.followup.send(
            f"Painel publicado. As candidaturas chegam em {canal_respostas.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotaoCandidatar, BotaoDecisao)
    await bot.add_cog(Candidaturas(bot))
