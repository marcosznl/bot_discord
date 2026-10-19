"""Carrega o bot inteiro sem conectar no Discord e serializa comandos e
paineis do jeito que seriam enviados - pega nome/descricao invalidos,
limites de componentes e erros de import antes de subir o bot de verdade."""

import asyncio
from types import SimpleNamespace

import discord
import pytest


@pytest.fixture
def bot_carregado():
    import main

    async def montar():
        await main.bot._async_setup_hook()
        await main.bot.setup_hook()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(montar())
    yield main.bot, loop
    for extensao in list(main.bot.extensions):
        loop.run_until_complete(main.bot.unload_extension(extensao))
    loop.close()


def test_comandos_serializam(bot_carregado):
    bot, _ = bot_carregado
    nomes = set()
    for tipo in discord.AppCommandType:
        for comando in bot.tree.get_commands(type=tipo):
            comando.to_dict(bot.tree)
            nomes.add(comando.name)

    esperados = {
        "ping", "ajuda", "usuario", "avatar", "servidor", "mod", "logs", "automod", "boasvindas",
        "enquete", "cargos", "tickets", "evento", "lembrete", "xp", "interagir", "gif", "musica",
        "Ver perfil", "Ver avatar", "Ver rank XP", "Denunciar mensagem", "Fixar ou desafixar",
    }
    assert esperados <= nomes
    assert not {"status", "socao", "abraco", "charme", "limpar"} & nomes


def test_ajuda_cobre_todos_os_comandos(bot_carregado):
    from bot.cogs.geral import COMANDOS_CATEGORIAS

    bot, _ = bot_carregado
    na_ajuda = {nome for nomes in COMANDOS_CATEGORIAS.values() for nome in nomes}
    registrados = {comando.name for comando in bot.tree.get_commands(type=discord.AppCommandType.chat_input)}
    assert na_ajuda == registrados


def test_paineis_serializam(bot_carregado):
    from bot.cogs.comunidade import painel_tickets
    from bot.cogs.geral import painel_ajuda
    from bot.cogs.moderacao import PainelAutoMod
    from bot.cogs.musica import PainelMusica
    from bot.cogs.xp import BotoesXP

    bot, loop = bot_carregado

    async def montar():
        guild_sem_voz = SimpleNamespace(id=1, voice_client=None)
        return [
            painel_ajuda(bot),
            BotoesXP(),
            PainelMusica(guild_sem_voz),
            PainelAutoMod(bot, {}),
            painel_tickets(None, 0),
        ]

    for view in loop.run_until_complete(montar()):
        componentes = view.to_components()
        assert componentes
        assert view.content_length() <= 4000


def test_paineis_com_botoes_persistentes(bot_carregado):
    from bot.cogs.comunidade import BotaoFecharTicket, painel_cargos
    from bot.cogs.diversao import BotaoRetribuir

    _, loop = bot_carregado

    async def montar():
        cargos = [
            SimpleNamespace(id=1000 + numero, name=f"Cargo {numero}", color=discord.Color.default())
            for numero in range(20)
        ]
        painel = painel_cargos("Cores", None, cargos)
        retribuir = discord.ui.View(timeout=None)
        retribuir.add_item(BotaoRetribuir("abraco", 1, 2))
        fechar = discord.ui.LayoutView(timeout=None)
        fechar.add_item(discord.ui.Container(discord.ui.ActionRow(BotaoFecharTicket(5))))
        return painel, retribuir, fechar

    painel, retribuir, fechar = loop.run_until_complete(montar())
    ids = [item.custom_id for item in painel.walk_children() if isinstance(item, discord.ui.DynamicItem)]
    assert ids == [f"cargo:{1000 + numero}" for numero in range(20)]
    assert retribuir.to_components()[0]["components"][0]["custom_id"] == "interagir:abraco:1:2"
    assert fechar.to_components()


def test_regras_automod_montam():
    from bot.cogs import moderacao

    for nome in (moderacao.REGRA_PALAVROES, moderacao.REGRA_SPAM, moderacao.REGRA_MENCOES, moderacao.REGRA_PALAVRAS):
        gatilho = moderacao._gatilho_regra(nome, ["feio"])
        gatilho.to_metadata_dict()
        for acao in moderacao._acoes_regra(nome, 123):
            acao.to_dict()
