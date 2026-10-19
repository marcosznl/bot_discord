import bot.cogs.xp as xp
from bot.layout import barra_progresso


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
    assert barra_progresso(0, 100) == "░" * 20


def test_barra_progresso_cheia():
    assert barra_progresso(100, 100) == "█" * 20


def test_barra_progresso_metade():
    assert barra_progresso(50, 100, tamanho=10) == "█" * 5 + "░" * 5


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


def test_xp_migra_banco_com_ranking_semanal(tmp_path, monkeypatch):
    import sqlite3

    caminho = str(tmp_path / "xp_antigo.db")
    conexao = sqlite3.connect(caminho)
    conexao.execute(
        "CREATE TABLE xp_usuarios (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
        "xp INTEGER NOT NULL DEFAULT 0, xp_semana INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id))"
    )
    conexao.execute("CREATE TABLE xp_periodo (guild_id INTEGER PRIMARY KEY, semana TEXT)")
    conexao.execute("INSERT INTO xp_usuarios VALUES (1, 2, 300, 40)")
    conexao.commit()
    conexao.close()

    monkeypatch.setattr(xp, "XP_DB_PATH", caminho)
    assert xp._xp_obter(1, 2) == 300
    xp._xp_adicionar(1, 2, 10)
    assert xp._xp_obter(1, 2) == 310

    conexao = sqlite3.connect(caminho)
    colunas = [linha[1] for linha in conexao.execute("PRAGMA table_info(xp_usuarios)")]
    tabelas = [linha[0] for linha in conexao.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    conexao.close()
    assert "xp_semana" not in colunas
    assert "xp_periodo" not in tabelas
