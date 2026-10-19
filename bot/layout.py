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
