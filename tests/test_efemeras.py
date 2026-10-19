import asyncio

import discord
from discord.utils import MISSING

from bot import efemeras


def test_prazo_por_tipo_de_resposta():
    assert efemeras.prazo({"ephemeral": True}) == efemeras.SEGUNDOS_CONFIRMACAO
    assert efemeras.prazo({"view": discord.ui.LayoutView()}) == efemeras.SEGUNDOS_PAINEL
    assert efemeras.prazo({"file": discord.File(__file__)}) == efemeras.SEGUNDOS_PAINEL
    assert efemeras.prazo({"view": MISSING, "files": []}) == efemeras.SEGUNDOS_CONFIRMACAO
    # O Discord so deixa apagar enquanto o token vale (15 min).
    assert efemeras.SEGUNDOS_PAINEL < 15 * 60


def test_respostas_efemeras_ganham_prazo(monkeypatch):
    recebidos = []

    async def responder_falso(self, content=None, **kwargs):
        recebidos.append(kwargs)

    monkeypatch.setattr(discord.InteractionResponse, "send_message", responder_falso)
    # Guarda os originais para o teste nao deixar a lib embrulhada duas vezes.
    monkeypatch.setattr(discord.Webhook, "send", discord.Webhook.send)
    monkeypatch.setattr(efemeras, "_instalado", False)
    efemeras.instalar()

    async def enviar():
        resposta = object.__new__(discord.InteractionResponse)
        await discord.InteractionResponse.send_message(resposta, "ok", ephemeral=True)
        await discord.InteractionResponse.send_message(resposta, "publica")
        await discord.InteractionResponse.send_message(resposta, "fica", ephemeral=True, delete_after=60)

    asyncio.run(enviar())
    assert recebidos[0]["delete_after"] == efemeras.SEGUNDOS_CONFIRMACAO
    assert "delete_after" not in recebidos[1]  # mensagem publica nao some
    assert recebidos[2]["delete_after"] == 60  # prazo explicito e respeitado
