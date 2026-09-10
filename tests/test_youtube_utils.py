import bot.cogs.youtube as youtube


def test_opcoes_pasta_inclui_pasta_principal_subpastas_e_criar_nova():
    opcoes = youtube._youtube_opcoes_pasta(["marcos", "victor"])
    assert [o.value for o in opcoes] == ["_raiz", "marcos", "victor", "_nova"]
    assert opcoes[0].label == "Pasta principal (sem subpasta)"


def test_nome_pasta_valido_aceita_nome_simples():
    assert youtube._youtube_nome_pasta_valido("familia") is True


def test_nome_pasta_valido_rejeita_vazio_e_pontos():
    assert youtube._youtube_nome_pasta_valido("") is False
    assert youtube._youtube_nome_pasta_valido(".") is False
    assert youtube._youtube_nome_pasta_valido("..") is False


def test_nome_pasta_valido_rejeita_separador_de_caminho():
    assert youtube._youtube_nome_pasta_valido("a/b") is False
    assert youtube._youtube_nome_pasta_valido("a\\b") is False


def test_pastas_disponiveis_lista_apenas_subpastas(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    (tmp_path / "victor").mkdir()
    (tmp_path / "marcos").mkdir()
    (tmp_path / ".oculta").mkdir()
    (tmp_path / "arquivo.txt").write_text("x")

    assert youtube._youtube_pastas_disponiveis() == ["marcos", "victor"]


def test_pastas_disponiveis_pasta_download_inexistente_devolve_vazio(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path / "nao-existe"))
    assert youtube._youtube_pastas_disponiveis() == []


def test_pasta_destino_raiz_quando_sem_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    assert youtube._youtube_pasta_destino("") == str(tmp_path)


def test_pasta_destino_cria_subpasta_se_nao_existir(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    destino = youtube._youtube_pasta_destino("victor")
    assert destino == str(tmp_path / "victor")
    assert (tmp_path / "victor").is_dir()
