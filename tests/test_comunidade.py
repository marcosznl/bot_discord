import datetime

import bot.cogs.comunidade as comunidade
from bot.config import FUSO

AGORA = datetime.datetime(2026, 10, 19, 15, 0, tzinfo=FUSO)


def test_duracao():
    assert comunidade.interpretar_duracao("10m") == 600
    assert comunidade.interpretar_duracao("10 min") == 600
    assert comunidade.interpretar_duracao("1h30m") == 5400
    assert comunidade.interpretar_duracao("2d") == 172800
    assert comunidade.interpretar_duracao("") is None
    assert comunidade.interpretar_duracao("20") is None
    assert comunidade.interpretar_duracao("amanha") is None


def test_data_dia_e_hora():
    assert comunidade.interpretar_data("25/12 20:00", AGORA) == datetime.datetime(2026, 12, 25, 20, 0, tzinfo=FUSO)
    assert comunidade.interpretar_data("25/12/27 20h", AGORA) == datetime.datetime(2027, 12, 25, 20, 0, tzinfo=FUSO)


def test_data_ja_passou_no_ano_vai_pro_proximo():
    assert comunidade.interpretar_data("01/02 10:00", AGORA).year == 2027


def test_data_relativa():
    assert comunidade.interpretar_data("amanha 9h", AGORA) == datetime.datetime(2026, 10, 20, 9, 0, tzinfo=FUSO)
    assert comunidade.interpretar_data("hoje 19:30", AGORA) == datetime.datetime(2026, 10, 19, 19, 30, tzinfo=FUSO)
    # So a hora: se ja passou hoje, e amanha.
    assert comunidade.interpretar_data("10:00", AGORA) == datetime.datetime(2026, 10, 20, 10, 0, tzinfo=FUSO)


def test_data_invalida():
    assert comunidade.interpretar_data("31/02 10:00", AGORA) is None
    assert comunidade.interpretar_data("25:00", AGORA) is None
    assert comunidade.interpretar_data("qualquer coisa", AGORA) is None


def test_quando_aceita_duracao_e_data():
    assert comunidade.interpretar_quando("10m", AGORA) == AGORA + datetime.timedelta(minutes=10)
    assert comunidade.interpretar_quando("amanha 9h", AGORA).day == 20


def test_opcoes_enquete():
    assert comunidade.opcoes_enquete("Pizza; Hamburguer ;; Sushi ") == ["Pizza", "Hamburguer", "Sushi"]
    assert len(comunidade.opcoes_enquete("x" * 80 + ";b")[0]) == 55


def test_lembretes(tmp_path, monkeypatch):
    monkeypatch.setattr(comunidade, "COMUNIDADE_DB_PATH", str(tmp_path / "comunidade.db"))

    primeiro = comunidade.lembrete_criar(1, 10, "beber agua", 100.0)
    comunidade.lembrete_criar(1, None, "dormir", 500.0)
    assert [texto for _, texto, _ in comunidade.lembrete_listar(1)] == ["beber agua", "dormir"]

    assert [lembrete[0] for lembrete in comunidade.lembrete_vencidos(200.0)] == [primeiro]

    assert comunidade.lembrete_cancelar(2, primeiro) is False  # lembrete de outra pessoa
    assert comunidade.lembrete_cancelar(1, primeiro) is True
    assert len(comunidade.lembrete_listar(1)) == 1


def test_lembretes_limite(tmp_path, monkeypatch):
    monkeypatch.setattr(comunidade, "COMUNIDADE_DB_PATH", str(tmp_path / "comunidade.db"))
    for _ in range(comunidade.LEMBRETES_MAX_POR_USUARIO):
        assert comunidade.lembrete_criar(1, None, "x", 1.0) is not None
    assert comunidade.lembrete_criar(1, None, "x", 1.0) is None
