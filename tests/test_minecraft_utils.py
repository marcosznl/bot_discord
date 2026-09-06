from bot.cogs.minecraft import (
    _formatar_duracao,
    _mc_escrever_propriedade,
    _mc_ler_propriedade,
)


def test_formatar_duracao_minutos():
    assert _formatar_duracao(125) == "2m"


def test_formatar_duracao_horas():
    assert _formatar_duracao(3725) == "1h2m"


def test_ler_e_escrever_propriedade(tmp_path):
    config = {"dir": str(tmp_path)}
    (tmp_path / "server.properties").write_text("motd=Ola\nmax-players=20\n")

    assert _mc_ler_propriedade(config, "motd") == "Ola"
    assert _mc_ler_propriedade(config, "inexistente") is None

    _mc_escrever_propriedade(config, "motd", "Novo nome")
    assert _mc_ler_propriedade(config, "motd") == "Novo nome"

    _mc_escrever_propriedade(config, "nova-chave", "valor")
    assert _mc_ler_propriedade(config, "nova-chave") == "valor"


def test_ler_propriedade_arquivo_inexistente(tmp_path):
    config = {"dir": str(tmp_path)}
    assert _mc_ler_propriedade(config, "motd") is None
