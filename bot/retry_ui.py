import logging
from typing import Awaitable, Callable

import discord

logger = logging.getLogger(__name__)

Retomar = Callable[[discord.Interaction], Awaitable[None]]


def interacao_expirou(error: Exception, interaction: discord.Interaction) -> bool:
    # O Discord so espera ~3s pela primeira resposta de uma interacao - depois
    # disso, qualquer resposta falha com NotFound 10062 (Unknown interaction).
    # interaction.response.is_done() so fica True depois que a PRIMEIRA
    # resposta e aceita - se ainda for False, nada rodou alem da primeira
    # linha, entao e seguro reexecutar do zero sem duplicar efeito colateral.
    return (
        isinstance(error, discord.NotFound)
        and error.code == 10062
        and not interaction.response.is_done()
    )


class TentarNovamente(discord.ui.View):
    """Botao unico que reexecuta uma acao com uma interacao nova - usado
    quando a interacao original expirou antes do bot conseguir responder."""

    def __init__(self, retomar: Retomar, autor_id: int):
        super().__init__(timeout=300)
        self.retomar = retomar
        self.autor_id = autor_id
        self.mensagem: discord.Message | None = None

    @discord.ui.button(label="Tentar de novo", style=discord.ButtonStyle.primary)
    async def tentar(self, interaction: discord.Interaction, botao: discord.ui.Button) -> None:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "So quem fez o pedido original pode tentar de novo.", ephemeral=True
            )
            return

        for filho in self.children:
            filho.disabled = True
        if self.mensagem is not None:
            await self.mensagem.edit(view=self)
        self.stop()

        await self.retomar(interaction)


async def oferecer_tentar_de_novo(interaction: discord.Interaction, retomar: Retomar, alvo: str = "") -> None:
    if interaction.channel is None:
        return
    view = TentarNovamente(retomar, interaction.user.id)
    try:
        view.mensagem = await interaction.channel.send(
            f"{interaction.user.mention} a interacao expirou antes de eu conseguir responder{alvo} "
            "(o Discord so espera 3s pela primeira resposta).",
            view=view,
        )
    except discord.HTTPException as erro:
        # Canal onde o bot nao pode escrever (ou instalacao de usuario).
        logger.warning(f"Nao consegui oferecer 'Tentar de novo' em {interaction.channel_id}: {erro}")


class _ExpiracaoMixin:
    """Quando um clique ou formulario expira antes do bot responder, so
    registra no log. Diferente dos comandos, aqui nao da pra reexecutar: o
    callback responderia editando a mensagem errada (a do aviso, nao a do
    painel). O painel continua funcionando - basta clicar de novo."""

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        if interacao_expirou(error, interaction):
            logger.warning(f"Clique expirou antes da resposta em {self!r} ({item!r})")
            return
        logger.error(f"Ignorando excecao na view {self!r} para o item {item!r}", exc_info=error)


class ViewDoBot(_ExpiracaoMixin, discord.ui.View):
    """Base para as Views classicas (botoes/selects soltos) do bot."""


class LayoutViewDoBot(_ExpiracaoMixin, discord.ui.LayoutView):
    """Base para as Views no layout novo do Discord (Components V2:
    containers, secoes, textos e miniaturas)."""


class ModalDoBot(discord.ui.Modal):
    """Base para os Modals do bot - mesmo tratamento de expiracao das Views."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if interacao_expirou(error, interaction):
            logger.warning(f"Formulario expirou antes da resposta em {self!r}")
            return
        logger.error(f"Ignorando excecao no modal {self!r}", exc_info=error)
