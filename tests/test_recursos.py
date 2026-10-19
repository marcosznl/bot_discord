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
