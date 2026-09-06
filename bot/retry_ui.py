import logging
from typing import Awaitable, Callable

import discord

logger = logging.getLogger(__name__)


def _interacao_expirou(error: Exception, interaction: discord.Interaction) -> bool:
    # O Discord so espera ~3s pela primeira resposta de uma interacao - depois
    # disso, qualquer resposta falha com NotFound 10062 (Unknown interaction).
    return (
        isinstance(error, discord.NotFound)
        and error.code == 10062
        and not interaction.response.is_done()
    )


class TentarNovamente(discord.ui.View):
    """Botao unico que reexecuta uma acao com uma interacao nova - usado
    quando a interacao original expirou antes do bot conseguir responder."""

    def __init__(self, retomar: Callable[[discord.Interaction], Awaitable[None]], autor_id: int):
        super().__init__(timeout=300)
        self.retomar = retomar
        self.autor_id = autor_id
        self.mensagem: discord.Message | None = None

    @discord.ui.button(label="Tentar de novo", style=discord.ButtonStyle.primary)
    async def tentar(self, interaction: discord.Interaction, botao: discord.ui.Button) -> None:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "So quem clicou originalmente pode tentar de novo.", ephemeral=True
            )
            return

        for filho in self.children:
            filho.disabled = True
        if self.mensagem is not None:
            await self.mensagem.edit(view=self)
        self.stop()

        await self.retomar(interaction)


async def _oferecer_tentar_de_novo(
    interaction: discord.Interaction, retomar: Callable[[discord.Interaction], Awaitable[None]]
) -> None:
    if interaction.channel is None:
        return
    view = TentarNovamente(retomar, interaction.user.id)
    view.mensagem = await interaction.channel.send(
        f"{interaction.user.mention} a interacao expirou antes de eu conseguir responder "
        "(o Discord so espera 3s pela primeira resposta).",
        view=view,
    )


class ViewComRetry(discord.ui.View):
    """Base para as Views do bot: quando um clique expira antes do bot
    responder, oferece um botao pra tentar de novo em vez de so ignorar a
    excecao (comportamento padrao do discord.py)."""

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        if not _interacao_expirou(error, interaction):
            logger.error(f"Ignorando excecao na view {self!r} para o item {item!r}", exc_info=error)
            return
        await _oferecer_tentar_de_novo(interaction, item.callback)


class ModalComRetry(discord.ui.Modal):
    """Base para os Modals do bot - mesmo comportamento de retry da
    ViewComRetry, mas para o envio (on_submit) de um modal."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if not _interacao_expirou(error, interaction):
            logger.error(f"Ignorando excecao no modal {self!r}", exc_info=error)
            return
        await _oferecer_tentar_de_novo(interaction, self.on_submit)
