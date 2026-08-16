import bot.cogs.playlist as playlist


def test_playlist_adicionar_e_listar(tmp_path, monkeypatch):
    monkeypatch.setattr(playlist, "PLAYLIST_DB_PATH", str(tmp_path / "playlists_teste.db"))
    user_id = 1

    assert playlist._playlist_listar(user_id) == []

    playlist._playlist_adicionar(user_id, "favoritas", "Musica 1", "busca 1", 120)
    playlist._playlist_adicionar(user_id, "favoritas", "Musica 2", "busca 2", 200)

    assert playlist._playlist_listar(user_id) == [("favoritas", 2)]

    musicas = playlist._playlist_musicas(user_id, "favoritas")
    assert len(musicas) == 2
    assert musicas[0][1] == "Musica 1"
    assert musicas[1][1] == "Musica 2"


def test_playlist_remover_musica(tmp_path, monkeypatch):
    monkeypatch.setattr(playlist, "PLAYLIST_DB_PATH", str(tmp_path / "playlists_teste.db"))
    user_id = 1
    playlist._playlist_adicionar(user_id, "favoritas", "Musica 1", "busca 1", 120)

    rowid = playlist._playlist_musicas(user_id, "favoritas")[0][0]
    playlist._playlist_remover_musica(rowid)

    assert playlist._playlist_musicas(user_id, "favoritas") == []


def test_playlist_apagar(tmp_path, monkeypatch):
    monkeypatch.setattr(playlist, "PLAYLIST_DB_PATH", str(tmp_path / "playlists_teste.db"))
    user_id = 1
    playlist._playlist_adicionar(user_id, "favoritas", "Musica 1", "busca 1", 120)

    removidos = playlist._playlist_apagar(user_id, "favoritas")
    assert removidos == 1
    assert playlist._playlist_listar(user_id) == []


def test_playlist_apagar_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(playlist, "PLAYLIST_DB_PATH", str(tmp_path / "playlists_teste.db"))
    assert playlist._playlist_apagar(1, "nao existe") == 0
