from bot.jsonstore import escrever_json, ler_json


def test_ler_json_arquivo_inexistente_devolve_vazio(tmp_path):
    caminho = tmp_path / "nao_existe.json"
    assert ler_json(str(caminho)) == {}


def test_escrever_e_ler_json(tmp_path):
    caminho = tmp_path / "dados.json"
    escrever_json(str(caminho), {"a": 1, "b": [1, 2, 3]})
    assert ler_json(str(caminho)) == {"a": 1, "b": [1, 2, 3]}


def test_ler_json_invalido_devolve_vazio(tmp_path):
    caminho = tmp_path / "invalido.json"
    caminho.write_text("isso nao e json valido {{{")
    assert ler_json(str(caminho)) == {}
