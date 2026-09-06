import bot.cogs.xp as xp


def test_xp_total_ate_nivel_zero():
    assert xp._xp_total_ate_nivel(0) == 0


def test_xp_calcular_nivel_sobe_conforme_xp():
    assert xp._xp_calcular_nivel(0) == 0
    total_nivel_1 = xp._xp_total_ate_nivel(1)
    assert xp._xp_calcular_nivel(total_nivel_1) == 1
    assert xp._xp_calcular_nivel(total_nivel_1 - 1) == 0


def test_xp_progresso_no_nivel_zero():
    nivel, xp_no_nivel, xp_necessario = xp._xp_progresso(0)
    assert nivel == 0
    assert xp_no_nivel == 0
    assert xp_necessario == xp._xp_total_ate_nivel(1)


def test_barra_progresso_vazia():
    assert xp._barra_progresso(0, 100) == "░" * 20


def test_barra_progresso_cheia():
    assert xp._barra_progresso(100, 100) == "█" * 20


def test_barra_progresso_metade():
    assert xp._barra_progresso(50, 100, tamanho=10) == "█" * 5 + "░" * 5


def test_xp_adicionar_e_obter(tmp_path, monkeypatch):
    monkeypatch.setattr(xp, "XP_DB_PATH", str(tmp_path / "xp_teste.db"))
    guild_id, user_id = 1, 2

    antes, depois = xp._xp_adicionar(guild_id, user_id, 50)
    assert (antes, depois) == (0, 50)
    assert xp._xp_obter(guild_id, user_id) == 50

    xp._xp_adicionar(guild_id, user_id, 25)
    assert xp._xp_obter(guild_id, user_id) == 75


def test_xp_nao_fica_negativo(tmp_path, monkeypatch):
    monkeypatch.setattr(xp, "XP_DB_PATH", str(tmp_path / "xp_teste.db"))
    guild_id, user_id = 1, 2

    xp._xp_adicionar(guild_id, user_id, 10)
    _, depois = xp._xp_adicionar(guild_id, user_id, -100)
    assert depois == 0


def test_xp_ativo_desativo(tmp_path, monkeypatch):
    monkeypatch.setattr(xp, "XP_DB_PATH", str(tmp_path / "xp_teste.db"))
    guild_id = 1

    assert xp._xp_ativo(guild_id) is True

    xp._xp_definir_ativo(guild_id, False)
    assert xp._xp_ativo(guild_id) is False

    xp._xp_definir_ativo(guild_id, True)
    assert xp._xp_ativo(guild_id) is True


def test_xp_cargo_por_nivel(tmp_path, monkeypatch):
    monkeypatch.setattr(xp, "XP_DB_PATH", str(tmp_path / "xp_teste.db"))
    guild_id = 1

    assert xp._xp_cargo_do_nivel(guild_id, 5) is None

    xp._xp_cargo_definir(guild_id, 5, 999)
    assert xp._xp_cargo_do_nivel(guild_id, 5) == 999

    removido = xp._xp_cargo_remover(guild_id, 5)
    assert removido is True
    assert xp._xp_cargo_do_nivel(guild_id, 5) is None
