"""Atalhos para montar mensagens no layout novo do Discord (Components V2).

Uma mensagem com LayoutView nao aceita content nem embeds - tudo (texto,
miniatura, botoes) vai dentro dos componentes. Esses helpers deixam os cogs
montarem os paineis sem repetir a mesma burocracia de ui.* em todo lugar.
"""

from typing import Awaitable, Callable

import discord

Acao = Callable[[discord.Interaction], Awaitable[None]]


def botao(
    rotulo: str,
    acao: Acao,
    estilo: discord.ButtonStyle = discord.ButtonStyle.secondary,
    desativado: bool = False,
    emoji: discord.Emoji | str | None = None,
) -> discord.ui.Button:
    item = discord.ui.Button(label=rotulo, style=estilo, disabled=desativado, emoji=emoji)
    item.callback = acao
    return item


def linha(*itens: discord.ui.Item) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    for item in itens:
        row.add_item(item)
    return row


def texto(conteudo: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(conteudo)


def secao_com_miniatura(conteudo: str, url_imagem: str | None) -> discord.ui.Item:
    """Texto com uma imagem pequena a direita - ou so o texto se nao houver
    imagem (Section exige um acessorio)."""
    if not url_imagem:
        return texto(conteudo)
    return discord.ui.Section(conteudo, accessory=discord.ui.Thumbnail(url_imagem))


def separador() -> discord.ui.Separator:
    return discord.ui.Separator()


def view_estatica(*itens: discord.ui.Item, cor: discord.Color | int | None = None) -> discord.ui.LayoutView:
    """LayoutView sem nada clicavel, so pra exibir um cartao."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*itens, accent_color=cor))
    return view


def barra_progresso(atual: float, total: float, tamanho: int = 20) -> str:
    proporcao = min(atual / total, 1) if total else 0
    preenchido = round(tamanho * proporcao)
    return "█" * preenchido + "░" * (tamanho - preenchido)


# ---------------------------------------------------------------- botoes fixos
#
# Botoes de painel que nao dependem da memoria do bot: a acao mora no
# custom_id ("painel:musica:pular") e e procurada num registro na hora do
# clique. Continuam funcionando depois que o bot reinicia e nunca expiram -
# diferente de uma View comum, que some apos o timeout ou um reinicio.

_ACOES: dict[str, Acao] = {}


def registrar_acao(painel: str, acao: str, funcao: Acao) -> None:
    _ACOES[f"{painel}:{acao}"] = funcao


class BotaoFixo(discord.ui.DynamicItem[discord.ui.Button], template=r"painel:(?P<chave>[a-z_]+:[a-z_]+)"):
    def __init__(
        self,
        chave: str,
        rotulo: str = "",
        estilo: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: discord.Emoji | discord.PartialEmoji | str | None = None,
        desativado: bool = False,
    ):
        super().__init__(
            discord.ui.Button(
                label=rotulo, style=estilo, emoji=emoji, disabled=desativado, custom_id=f"painel:{chave}"
            )
        )
        self.chave = chave

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["chave"], item.label or "", item.style, item.emoji, item.disabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        funcao = _ACOES.get(self.chave)
        if funcao is None:
            await interaction.response.send_message("Esse botao nao existe mais. Abra o painel de novo.", ephemeral=True)
            return
        await funcao(interaction)


def botao_fixo(
    rotulo: str,
    painel: str,
    acao: str,
    estilo: discord.ButtonStyle = discord.ButtonStyle.secondary,
    emoji: discord.Emoji | str | None = None,
    desativado: bool = False,
) -> BotaoFixo:
    return BotaoFixo(f"{painel}:{acao}", rotulo, estilo, emoji, desativado)
