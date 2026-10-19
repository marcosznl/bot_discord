import bot.cogs.moderacao as moderacao


def test_advertencias(tmp_path, monkeypatch):
    monkeypatch.setattr(moderacao, "MOD_DB_PATH", str(tmp_path / "moderacao.db"))

    assert moderacao._adv_adicionar(1, 2, 99, "spam") == 1
    assert moderacao._adv_adicionar(1, 2, 99, "flood") == 2
    assert moderacao._adv_adicionar(1, 3, 99, "outro membro") == 1

    lista = moderacao._adv_listar(1, 2)
    assert [motivo for _, _, motivo, _ in lista] == ["spam", "flood"]

    assert moderacao._adv_limpar(1, 2) == 2
    assert moderacao._adv_listar(1, 2) == []
    assert len(moderacao._adv_listar(1, 3)) == 1


def test_canal_de_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(moderacao, "MOD_DB_PATH", str(tmp_path / "moderacao.db"))

    assert moderacao._logs_canal(1) is None
    moderacao._logs_definir(1, 555)
    assert moderacao._logs_canal(1) == 555
    moderacao._logs_definir(1, 777)
    assert moderacao._logs_canal(1) == 777
    moderacao._logs_definir(1, None)
    assert moderacao._logs_canal(1) is None


def test_palavras_proibidas():
    assert moderacao._palavras_de_texto("Feio, bobo,, FEIO\nchato ") == ["feio", "bobo", "chato"]
    assert moderacao._palavras_de_texto("") == []
