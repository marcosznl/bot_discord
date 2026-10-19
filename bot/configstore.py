"""Configuracao por servidor (chave -> valor JSON) para os recursos que so
precisam guardar um canal, um numero ou um pequeno dicionario."""

import json
from typing import Any

from bot import banco

banco.registrar_schema(
    "CREATE TABLE IF NOT EXISTS config_servidor ("
    "guild_id INTEGER NOT NULL, chave TEXT NOT NULL, valor TEXT NOT NULL, PRIMARY KEY (guild_id, chave))"
)


def obter(guild_id: int, chave: str, padrao: Any = None) -> Any:
    linha = banco.um("SELECT valor FROM config_servidor WHERE guild_id = ? AND chave = ?", (guild_id, chave))
    return json.loads(linha[0]) if linha else padrao


def definir(guild_id: int, chave: str, valor: Any) -> None:
    banco.executar(
        "INSERT INTO config_servidor (guild_id, chave, valor) VALUES (?, ?, ?) "
        "ON CONFLICT (guild_id, chave) DO UPDATE SET valor = excluded.valor",
        (guild_id, chave, json.dumps(valor)),
    )


def remover(guild_id: int, chave: str) -> None:
    banco.executar("DELETE FROM config_servidor WHERE guild_id = ? AND chave = ?", (guild_id, chave))


def servidores_com(chave: str) -> list[tuple[int, Any]]:
    return [
        (guild_id, json.loads(valor))
        for guild_id, valor in banco.todos("SELECT guild_id, valor FROM config_servidor WHERE chave = ?", (chave,))
    ]
