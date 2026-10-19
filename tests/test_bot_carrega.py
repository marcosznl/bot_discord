"""Carrega o bot inteiro sem conectar no Discord e serializa comandos e
paineis do jeito que seriam enviados - pega nome/descricao invalidos,
limites de componentes e erros de import antes de subir o bot de verdade."""

import asyncio
from types import SimpleNamespace

import discord
import pytest


@pytest.fixture
def bot_carregado(monkeypatch):
    import main

    async def sem_rede(_client):
        pass

    # Subir os emojis do bot precisa de login no Discord.
    monkeypatch.setattr(main.emojis, "sincronizar", sem_rede)

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
        "sorteio", "sugerir", "verificacao", "candidaturas", "som", "sala", "aniversario", "configurar",
        "palco", "emoji", "anuncio", "cargo_visual",
        "Ver perfil", "Ver avatar", "Ver rank XP", "Denunciar mensagem", "Fixar ou desafixar",
        "Adicionar emojis ao servidor",
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
    from bot.cogs.geral import COMANDOS_CATEGORIAS, PainelAjuda
    from bot.cogs.moderacao import PainelAutoMod
    from bot.cogs.musica import PainelMusica
    from bot.cogs.xp import BotoesXP

    bot, loop = bot_carregado

    async def montar():
        guild_sem_voz = SimpleNamespace(id=1, voice_client=None)
        return [
            *(PainelAjuda(bot, categoria) for categoria in [*COMANDOS_CATEGORIAS, "Clique direito"]),
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


def test_paineis_dos_recursos_novos(bot_carregado):
    from bot.cogs.candidaturas import ModalCandidatura
    from bot.cogs.sorteios import painel_sorteio
    from bot.cogs.sugestoes import painel_sugestao

    _, loop = bot_carregado

    async def montar():
        sorteio = {
            "id": 1, "premio": "Nitro", "termina_em": 2_000_000_000, "encerrado": 0, "ganhadores": [],
            "vencedores": 2, "cargo_requerido": None, "participantes": 5, "criador_id": 9,
        }
        sugestao = {"id": 3, "autor_id": 9, "texto": "Canal de memes", "status": "analise", "resposta": None, "sim": 4, "nao": 1}
        encerrado = dict(sorteio, encerrado=1, ganhadores=[10, 11])
        return [painel_sorteio(sorteio), painel_sorteio(encerrado), painel_sugestao(sugestao)], ModalCandidatura(["Moderacao", "Eventos"])

    views, modal = loop.run_until_complete(montar())
    for view in views:
        assert view.to_components()
    assert len(modal.to_components()) == 5


def test_traducao_cobre_todos_os_comandos(bot_carregado):
    from bot.traducao import DESCRICOES, NOMES

    bot, _ = bot_carregado
    faltando = []
    for tipo in discord.AppCommandType:
        for comando in bot.tree.get_commands(type=tipo):
            itens = [comando, *getattr(comando, "commands", [])]
            for item in itens:
                if item.name not in NOMES:
                    faltando.append(f"nome: {item.name}")
                descricao = getattr(item, "description", None)
                if descricao and descricao not in DESCRICOES:
                    faltando.append(f"descricao: {descricao}")
    assert not faltando


def test_tradutor_responde_so_em_ingles():
    from discord import app_commands

    from bot.traducao import TradutorIngles

    async def traduzir(texto, idioma, local):
        contexto = app_commands.TranslationContext(location=local, data=None)
        return await TradutorIngles().translate(app_commands.locale_str(texto), idioma, contexto)

    nome = app_commands.TranslationContextLocation.command_name
    assert asyncio.run(traduzir("musica", discord.Locale.american_english, nome)) == "music"
    assert asyncio.run(traduzir("musica", discord.Locale.brazil_portuguese, nome)) is None


def test_modal_de_anuncio(bot_carregado):
    from bot.cogs.anuncios import ModalAnuncio, montar_anuncio

    _, loop = bot_carregado

    async def montar():
        canal = SimpleNamespace(id=1, mention="#geral")
        dados = {"titulo": "Evento", "texto": "Sabado!", "botao": ["Inscreva-se", "https://x.com"], "cargo": 5, "imagem": "imagem.png"}
        return ModalAnuncio(canal, None, None, None), montar_anuncio(dados)

    modal, view = loop.run_until_complete(montar())
    assert len(modal.to_components()) == 4
    assert view.to_components()
