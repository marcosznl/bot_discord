from bot.cogs.musica import _formatar_duracao_musica


def test_formatar_duracao_sem_valor():
    assert _formatar_duracao_musica(None) == "?"
    assert _formatar_duracao_musica(0) == "?"


def test_formatar_duracao_minutos():
    assert _formatar_duracao_musica(65) == "1:05"


def test_formatar_duracao_horas():
    assert _formatar_duracao_musica(3661) == "1:01:01"
