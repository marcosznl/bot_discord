import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from bot import configstore, layout
from bot.cogs import aniversarios, contagem, destaques, moderacao, sugestoes, voz, xp
from bot.permissions import requer_manage_guild

ACOES_ADVERTENCIA = [
    app_commands.Choice(name="Silenciar", value="silenciar"),
    app_commands.Choice(name="Expulsar", value="expulsar"),
    app_commands.Choice(name="Banir", value="banir"),
]


def _canal(canal_id: int | None) -> str:
    return f"<#{canal_id}>" if canal_id else "desligado"


class Configuracao(commands.Cog):
    configurar = app_commands.Group(
        name="configurar",
        description="Liga e configura os recursos do bot neste servidor",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _salvar(self, interaction: discord.Interaction, chave: str, valor, texto: str) -> None:
        if valor is None:
            await asyncio.to_thread(configstore.remover, interaction.guild.id, chave)
        else:
            await asyncio.to_thread(configstore.definir, interaction.guild.id, chave, valor)
        await interaction.response.send_message(texto, ephemeral=True)

    @configurar.command(name="sugestoes", description="Canal (de texto ou forum) que recebe o /sugerir")
    @app_commands.describe(canal="Canal das sugestoes (vazio = desligar)")
    async def cfg_sugestoes(
        self, interaction: discord.Interaction, canal: discord.TextChannel | discord.ForumChannel | None = None
    ):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        texto = f"Sugestoes vao para {canal.mention}." if canal else "Sugestoes desligadas."
        if isinstance(canal, discord.ForumChannel):
            texto += " Cada sugestao vira um post com etiqueta de status."
        await self._salvar(interaction, sugestoes.CHAVE_CANAL, canal.id if canal else None, texto)

    @configurar.command(name="aniversarios", description="Canal dos parabens e cargo de aniversariante do dia")
    @app_commands.describe(canal="Onde dar os parabens (vazio = desligar)", cargo="Cargo dado so no dia do aniversario")
    async def cfg_aniversarios(
        self, interaction: discord.Interaction, canal: discord.TextChannel | None = None, cargo: discord.Role | None = None
    ):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        valor = {"canal": canal.id, "cargo": cargo.id if cargo else None} if canal else None
        texto = (
            f"Parabens em {canal.mention} as {aniversarios.HORA_DO_PARABENS}h"
            + (f", com o cargo {cargo.mention} no dia." if cargo else ".")
            if canal
            else "Aniversarios desligados."
        )
        await self._salvar(interaction, aniversarios.CHAVE_CONFIG, valor, texto)

    @configurar.command(name="destaques", description="Mural: mensagens com varias reacoes vao para um canal")
    @app_commands.describe(
        canal="Canal do mural (vazio = desligar)",
        minimo="Quantas reacoes para entrar no mural",
        emoji="Emoji que conta (padrao ⭐)",
    )
    async def cfg_destaques(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        minimo: app_commands.Range[int, 1, 50] = 3,
        emoji: str | None = None,
    ):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        emoji = (emoji or destaques.EMOJI_PADRAO).strip()
        valor = {"canal": canal.id, "minimo": minimo, "emoji": emoji} if canal else None
        texto = f"Mensagens com {minimo}x {emoji} vao para {canal.mention}." if canal else "Mural desligado."
        await self._salvar(interaction, destaques.CHAVE_CONFIG, valor, texto)

    @configurar.command(name="contagem", description="Canal do jogo de contagem (1, 2, 3...)")
    @app_commands.describe(canal="Canal da contagem (vazio = desligar)")
    async def cfg_contagem(self, interaction: discord.Interaction, canal: discord.TextChannel | None = None):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        cog = self.bot.get_cog("Contagem")
        if cog is not None:
            cog.esquecer_canal(interaction.guild.id)
        texto = f"Contagem em {canal.mention}. Comecem do 1!" if canal else "Contagem desligada."
        await self._salvar(interaction, contagem.CHAVE_CANAL, canal.id if canal else None, texto)

    @configurar.command(name="salas", description="Canal de voz que cria uma sala temporaria para quem entrar")
    @app_commands.describe(canal="Canal de voz \"Criar sala\" (vazio = desligar)")
    async def cfg_salas(self, interaction: discord.Interaction, canal: discord.VoiceChannel | None = None):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        texto = (
            f"Quem entrar em {canal.mention} ganha uma sala propria, que some quando esvaziar. "
            "Preciso de Gerenciar Canais e Mover Membros."
            if canal
            else "Salas temporarias desligadas."
        )
        await self._salvar(interaction, voz.CHAVE_CRIADOR, canal.id if canal else None, texto)

    @configurar.command(name="advertencias", description="Validade das advertencias e punicao automatica")
    @app_commands.describe(
        limite="Advertencias ativas para punir automaticamente (0 = nunca)",
        acao="O que fazer ao atingir o limite",
        duracao_min="Minutos de silencio, se a acao for silenciar",
        validade_dias="Dias ate uma advertencia expirar (0 = nunca expira)",
    )
    @app_commands.choices(acao=ACOES_ADVERTENCIA)
    async def cfg_advertencias(
        self,
        interaction: discord.Interaction,
        limite: app_commands.Range[int, 0, 20],
        acao: app_commands.Choice[str] | None = None,
        duracao_min: app_commands.Range[int, 1, 40320] = 60,
        validade_dias: app_commands.Range[int, 0, 3650] = 0,
    ):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        acao_valor = acao.value if acao else "silenciar"
        valor = {"limite": limite, "acao": acao_valor, "duracao_min": duracao_min, "validade_dias": validade_dias}
        partes = []
        if limite:
            punicao = f"silencio de {duracao_min} min" if acao_valor == "silenciar" else acao_valor
            partes.append(f"Com {limite} advertencias ativas: {punicao}.")
        else:
            partes.append("Sem punicao automatica.")
        partes.append(f"Advertencias expiram em {validade_dias} dia(s)." if validade_dias else "Advertencias nao expiram.")
        await self._salvar(interaction, moderacao.CHAVE_ADVERTENCIAS, valor, " ".join(partes))

    @configurar.command(name="xp_voz", description="Liga ou desliga o XP por tempo em call")
    @app_commands.describe(ativo="Ganhar XP em call?")
    async def cfg_xp_voz(self, interaction: discord.Interaction, ativo: bool):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        texto = "XP em call ligado." if ativo else "XP em call desligado."
        await self._salvar(interaction, xp.CHAVE_XP_VOZ, ativo, texto)

    @configurar.command(name="ver", description="Mostra como o bot esta configurado neste servidor")
    async def ver(self, interaction: discord.Interaction):
        if not await requer_manage_guild(interaction, "esse comando"):
            return
        guild_id = interaction.guild.id

        def ler():
            obter = configstore.obter
            return {
                "logs": moderacao._logs_canal(guild_id),
                "sugestoes": obter(guild_id, sugestoes.CHAVE_CANAL),
                "aniversarios": obter(guild_id, aniversarios.CHAVE_CONFIG) or {},
                "destaques": obter(guild_id, destaques.CHAVE_CONFIG) or {},
                "contagem": obter(guild_id, contagem.CHAVE_CANAL),
                "salas": obter(guild_id, voz.CHAVE_CRIADOR),
                "advertencias": obter(guild_id, moderacao.CHAVE_ADVERTENCIAS) or {},
                "xp_voz": obter(guild_id, xp.CHAVE_XP_VOZ, True),
                "xp": xp._xp_ativo(guild_id),
            }

        c = await asyncio.to_thread(ler)
        adv = c["advertencias"]
        linhas = [
            f"**Logs:** {_canal(c['logs'])}",
            f"**Sugestoes:** {_canal(c['sugestoes'])}",
            f"**Aniversarios:** {_canal(c['aniversarios'].get('canal'))}"
            + (f" (cargo <@&{c['aniversarios']['cargo']}>)" if c["aniversarios"].get("cargo") else ""),
            f"**Mural de destaques:** {_canal(c['destaques'].get('canal'))}"
            + (f" ({c['destaques'].get('minimo')}x {c['destaques'].get('emoji')})" if c["destaques"] else ""),
            f"**Contagem:** {_canal(c['contagem'])}",
            f"**Salas temporarias:** {_canal(c['salas'])}",
            f"**XP:** {'ligado' if c['xp'] else 'desligado'} | **XP em call:** {'ligado' if c['xp_voz'] else 'desligado'}",
            "**Advertencias:** "
            + (f"punicao com {adv['limite']} ({adv['acao']})" if adv.get("limite") else "sem punicao automatica")
            + (f", expiram em {adv['validade_dias']} dia(s)" if adv.get("validade_dias") else ""),
        ]
        view = layout.view_estatica(
            layout.texto("## Configuracao do servidor\n" + "\n".join(linhas)),
            layout.separador(),
            layout.texto(
                "-# Mude com `/configurar ...`. Logs: `/logs` | AutoMod: `/automod` | Boas-vindas: `/boasvindas`"
            ),
            cor=discord.Color.blue(),
        )
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Configuracao(bot))
