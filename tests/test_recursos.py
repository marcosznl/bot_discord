import datetime

import pytest

from bot import banco, configstore


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    monkeypatch.setattr(banco, "BOT_DB_PATH", str(tmp_path / "bot.db"))


def test_configstore():
    assert configstore.obter(1, "x") is None
    assert configstore.obter(1, "x", True) is True
    configstore.definir(1, "x", {"canal": 5})
    configstore.definir(2, "x", {"canal": 6})
    assert configstore.obter(1, "x") == {"canal": 5}
    assert sorted(configstore.servidores_com("x")) == [(1, {"canal": 5}), (2, {"canal": 6})]
    configstore.remover(1, "x")
    assert configstore.obter(1, "x") is None


def test_sorteio():
    from bot.cogs import sorteios

    sid = sorteios.sorteio_criar(1, 2, 3, "Nitro", 2, None, 100.0)
    assert sorteios.sorteio_alternar_participacao(sid, 10) is True
    assert sorteios.sorteio_alternar_participacao(sid, 11) is True
    assert sorteios.sorteio_alternar_participacao(sid, 12) is True
    assert sorteios.sorteio_alternar_participacao(sid, 12) is False  # clicou de novo = saiu
    assert sorteios.sorteio_obter(sid)["participantes"] == 2
    assert sorteios.sorteios_vencidos(50.0) == []
    assert sorteios.sorteios_vencidos(150.0) == [sid]

    ganhadores = sorteios.sorteio_sortear(sid, 5)
    assert sorted(ganhadores) == [10, 11]
    sorteios.sorteio_encerrar(sid, [10])
    assert sorteios.sorteio_obter(sid)["ganhadores"] == [10]
    assert sorteios.sorteio_sortear(sid, 1, excluir={10}) == [11]
    assert sorteios.sorteios_vencidos(150.0) == []


def test_sugestao_votos():
    from bot.cogs import sugestoes

    sid = sugestoes.sugestao_criar(1, 9, "Canal de memes")
    assert sugestoes.sugestao_votar(sid, 1, 1) == 1
    assert sugestoes.sugestao_votar(sid, 2, -1) == -1
    assert sugestoes.sugestao_votar(sid, 2, 1) == 1  # trocou o voto
    assert sugestoes.sugestao_votar(sid, 1, 1) == 0  # tirou o voto
    sugestao = sugestoes.sugestao_obter(sid)
    assert (sugestao["sim"], sugestao["nao"]) == (1, 0)
    sugestoes.sugestao_definir_status(sid, "aprovada", "Feito!")
    assert sugestoes.sugestao_obter(sid)["status"] == "aprovada"


def test_contagem():
    from bot.cogs.contagem import EstadoContagem, avaliar

    estado = EstadoContagem()
    resultado, estado = avaliar(estado, 1, 1)
    assert resultado == "ok"
    resultado, estado = avaliar(estado, 2, 2)
    assert resultado == "recorde" and estado.recorde == 2
    resultado, estado = avaliar(estado, 2, 3)
    assert resultado == "repetiu" and estado.atual == 0 and estado.recorde == 2
    resultado, estado = avaliar(estado, 1, 5)
    assert resultado == "numero_errado" and estado.atual == 0
    _, estado = avaliar(estado, 1, 1)
    resultado, _ = avaliar(estado, 2, 2)
    assert resultado == "ok"  # igualou o recorde, nao bateu


def test_contagem_banco():
    from bot.cogs import contagem

    assert contagem.contagem_obter(1) == contagem.EstadoContagem()
    contagem.contagem_salvar(1, contagem.EstadoContagem(7, 3, 10))
    assert contagem.contagem_obter(1) == contagem.EstadoContagem(7, 3, 10)


def test_aniversarios():
    from bot.cogs import aniversarios

    assert aniversarios.interpretar_dia_mes("25/12") == (25, 12)
    assert aniversarios.interpretar_dia_mes("29/02") == (29, 2)
    assert aniversarios.interpretar_dia_mes("31/04") is None
    assert aniversarios.interpretar_dia_mes("natal") is None

    assert aniversarios.comemora_hoje(25, 12, datetime.date(2026, 12, 25))
    assert not aniversarios.comemora_hoje(25, 12, datetime.date(2026, 12, 24))
    # 29/02 comemora em 28/02 quando o ano nao e bissexto.
    assert aniversarios.comemora_hoje(29, 2, datetime.date(2027, 2, 28))
    assert not aniversarios.comemora_hoje(29, 2, datetime.date(2028, 2, 28))

    hoje = datetime.date(2026, 12, 20)
    assert aniversarios.dias_ate(25, 12, hoje) == 5
    assert aniversarios.dias_ate(1, 1, hoje) == 12

    aniversarios.aniversario_definir(1, 2, 25, 12)
    aniversarios.aniversario_definir(1, 2, 26, 12)  # corrigiu a data
    assert [linha[:3] for linha in aniversarios.aniversarios_do_servidor(1)] == [(2, 26, 12)]
    assert aniversarios.aniversario_remover(1, 2) is True
    assert aniversarios.aniversario_remover(1, 2) is False


def test_salas_temporarias():
    from bot.cogs import voz

    voz.sala_registrar(100, 1, 9)
    assert voz.sala_dono(100) == 9
    voz.sala_registrar(100, 1, 10)  # transferiu
    assert voz.sala_dono(100) == 10
    voz.sala_remover(100)
    assert voz.sala_dono(100) is None


def test_candidatura_so_decide_uma_vez():
    from bot.cogs import candidaturas

    cid = candidaturas.candidatura_criar(1, 2, "Moderacao")
    assert candidaturas.candidatura_pendente(1, 2)
    assert candidaturas.candidatura_definir_status(cid, "aprovada") is True
    assert candidaturas.candidatura_definir_status(cid, "recusada") is False
    assert not candidaturas.candidatura_pendente(1, 2)


def test_fila_de_musica_salva(tmp_path, monkeypatch):
    from bot.cogs import musica

    monkeypatch.setattr(musica, "FILA_PATH", str(tmp_path / "fila.json"))
    monkeypatch.setattr(musica, "_filas_musica", {1: [musica.Faixa("A", "a", 60, "https://x/a", None)], 2: []})
    monkeypatch.setattr(musica, "_canal_voz", {1: 500, 2: 600})
    musica._salvar_filas()
    assert musica._carregar_filas() == {1: (500, [musica.Faixa("A", "a", 60, "https://x/a", None)])}


def test_anuncios_agendados():
    from bot.cogs import anuncios

    assert anuncios.interpretar_botao("Inscreva-se | https://site.com") == ("Inscreva-se", "https://site.com")
    assert anuncios.interpretar_botao("sem link") is None
    assert anuncios.interpretar_botao("Texto | ftp://x") is None

    aid = anuncios.anuncio_agendar(1, 10, 9, {"titulo": "Oi", "texto": "..."}, 100.0)
    assert anuncios.anuncios_vencidos(50.0) == []
    assert [a[0] for a in anuncios.anuncios_vencidos(150.0)] == [aid]
    assert anuncios.anuncio_apagar(aid, guild_id=2) is None  # outro servidor
    assert anuncios.anuncio_apagar(aid, guild_id=1)["titulo"] == "Oi"
    assert anuncios.anuncios_pendentes(1) == []


def test_evento_avisado_uma_vez():
    from bot.cogs import anuncios

    assert not anuncios.evento_ja_avisado(5)
    anuncios.evento_marcar_avisado(5)
    anuncios.evento_marcar_avisado(5)
    assert anuncios.evento_ja_avisado(5)


def test_enquetes_registradas():
    from bot.cogs import comunidade

    comunidade.enquete_registrar(1, 10, 100.0)
    assert comunidade.enquetes_vencidas(150.0) == [(1, 10, 0)]
    comunidade.enquete_tentou_de_novo(1)
    assert comunidade.enquetes_vencidas(150.0) == []  # empurrou 60s
    assert comunidade.enquetes_vencidas(170.0) == [(1, 10, 1)]
    comunidade.enquete_esquecer(1)
    assert comunidade.enquetes_vencidas(1000.0) == []


def test_raid_detectado():
    import collections

    from bot.cogs.moderacao import raid_detectado

    entradas = collections.deque()
    assert not raid_detectado(entradas, 0, limite=3, segundos=10)
    assert not raid_detectado(entradas, 5, limite=3, segundos=10)
    assert raid_detectado(entradas, 9, limite=3, segundos=10)
    # Entradas antigas saem da janela.
    assert not raid_detectado(entradas, 30, limite=3, segundos=10)


def test_emojis_no_texto():
    from bot.cogs.expressoes import emojis_no_texto, nome_valido

    texto = "oi <:gato:123> e <a:danca:456> e de novo <:gato:123> e 😀"
    achados = emojis_no_texto(texto)
    assert [(e.name, e.id, e.animated) for e in achados] == [("gato", 123, False), ("danca", 456, True)]
    assert nome_valido("meu emoji!") == "meu_emoji_"
    assert nome_valido("a") is None


def test_nome_da_thread():
    from bot.cogs.servidor import nome_da_thread

    assert nome_da_thread("Meu desenho\nfeito ontem", "Ana") == "Meu desenho"
    assert nome_da_thread("", "Ana") == "Conversa de Ana"
    assert len(nome_da_thread("x" * 200, "Ana")) == 90


def test_backup(tmp_path):
    import datetime
    import sqlite3
    import zipfile

    from bot.cogs.manutencao import fazer_backup

    dados = tmp_path / "data"
    dados.mkdir()
    with sqlite3.connect(dados / "xp.db") as conexao:
        conexao.execute("CREATE TABLE t (x)")
        conexao.execute("INSERT INTO t VALUES (42)")
    (dados / "musica_fila.json").write_text("{}")
    extra = tmp_path / "boas_vindas.json"
    extra.write_text("{}")
    destino = tmp_path / "backups"

    for dia in range(1, 10):
        caminho = fazer_backup(str(dados), str(destino), [str(extra)], 7, datetime.date(2026, 10, dia))
    assert sorted(p.name for p in destino.iterdir())[0] == "backup-2026-10-03.zip"
    assert len(list(destino.iterdir())) == 7
    with zipfile.ZipFile(caminho) as arquivo_zip:
        assert sorted(arquivo_zip.namelist()) == ["boas_vindas.json", "data/musica_fila.json", "data/xp.db"]
        arquivo_zip.extract("data/xp.db", tmp_path / "restaurado")
    with sqlite3.connect(tmp_path / "restaurado" / "data" / "xp.db") as conexao:
        assert conexao.execute("SELECT x FROM t").fetchone() == (42,)
