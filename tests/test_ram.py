from bot import ram


class _FakeMemoria:
    def __init__(self, available_bytes: int):
        self.available = available_bytes


def test_ram_suficiente(monkeypatch):
    monkeypatch.setattr(ram.psutil, "virtual_memory", lambda: _FakeMemoria(8 * 1024**3))
    suficiente, disponivel = ram._ram_suficiente_para(4)
    assert suficiente is True
    assert disponivel == 8.0


def test_ram_insuficiente(monkeypatch):
    monkeypatch.setattr(ram.psutil, "virtual_memory", lambda: _FakeMemoria(2 * 1024**3))
    suficiente, disponivel = ram._ram_suficiente_para(4)
    assert suficiente is False
    assert disponivel == 2.0


def test_ram_considera_margem_de_seguranca(monkeypatch):
    # 4GB necessarios + 0.5GB de margem = precisa de 4.5GB disponiveis.
    monkeypatch.setattr(ram.psutil, "virtual_memory", lambda: _FakeMemoria(int(4.4 * 1024**3)))
    suficiente, _ = ram._ram_suficiente_para(4)
    assert suficiente is False

    monkeypatch.setattr(ram.psutil, "virtual_memory", lambda: _FakeMemoria(int(4.6 * 1024**3)))
    suficiente, _ = ram._ram_suficiente_para(4)
    assert suficiente is True
