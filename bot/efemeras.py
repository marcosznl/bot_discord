"""Respostas privadas ("So voce pode ver isso") somem sozinhas.

Em vez de passar delete_after em cada uma das centenas de respostas do bot,
a regra fica aqui, num lugar so: toda resposta efemera sem prazo definido
ganha um, conforme o tipo.

- Confirmacoes e avisos (so texto): somem em 15 s.
- Paineis (com botoes, menus, cartoes) e arquivos para baixar: 10 min.

O limite vem do proprio Discord: o bot so consegue apagar uma resposta
efemera enquanto o token da interacao vale, por 15 minutos.
"""

import discord
from discord.utils import MISSING

SEGUNDOS_CONFIRMACAO = 15
SEGUNDOS_PAINEL = 10 * 60

_instalado = False


def _tem(valor) -> bool:
    return valor is not MISSING and valor is not None and valor != []


def prazo(argumentos: dict) -> int:
    """Quanto tempo uma resposta efemera fica na tela, pelos argumentos do envio."""
    if any(_tem(argumentos.get(chave, MISSING)) for chave in ("view", "file", "files")):
        return SEGUNDOS_PAINEL
    return SEGUNDOS_CONFIRMACAO


def instalar() -> None:
    """Liga a regra para o bot inteiro. Chamado uma vez no main.py."""
    global _instalado
    if _instalado:
        return
    _instalado = True

    responder = discord.InteractionResponse.send_message

    async def send_message(self, content=None, **kwargs):
        if kwargs.get("ephemeral") and kwargs.get("delete_after") is None:
            kwargs["delete_after"] = prazo(kwargs)
        return await responder(self, content, **kwargs)

    discord.InteractionResponse.send_message = send_message

    # Followups (depois de um defer) nao aceitam delete_after: o jeito e
    # pedir a mensagem de volta (wait=True) e agendar a exclusao nela.
    enviar_webhook = discord.Webhook.send

    async def send(self, content=MISSING, **kwargs):
        if not kwargs.get("ephemeral"):
            return await enviar_webhook(self, content, **kwargs)
        quis_mensagem = kwargs.get("wait", False)
        kwargs["wait"] = True
        mensagem = await enviar_webhook(self, content, **kwargs)
        await mensagem.delete(delay=prazo(kwargs))
        return mensagem if quis_mensagem else None

    discord.Webhook.send = send
