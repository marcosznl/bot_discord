import os
import sqlite3

from bot import sqlitestore
from bot.config import DATA_DIR

# Banco unico dos recursos menores (sorteios, sugestoes, aniversarios,
# destaques, contagem, salas de voz, config por servidor). Os recursos
# maiores e mais antigos (xp, moderacao, comunidade) tem o proprio arquivo.
BOT_DB_PATH = os.path.join(DATA_DIR, "bot.db")

_schemas: list[str] = []
_prontos: set[str] = set()


def registrar_schema(sql: str) -> None:
    """Cada modulo registra o CREATE TABLE dele na importacao; tudo e criado
    na primeira conexao com o arquivo (e de novo se o caminho mudar, como
    nos testes)."""
    _schemas.append(sql)
    _prontos.clear()


def conectar() -> sqlite3.Connection:
    conexao = sqlite3.connect(BOT_DB_PATH)
    if BOT_DB_PATH not in _prontos:
        for sql in _schemas:
            conexao.executescript(sql)
        conexao.commit()
        _prontos.add(BOT_DB_PATH)
    return conexao


def executar(sql: str, params: tuple = ()) -> None:
    sqlitestore.executar(conectar, sql, params)


def executar_rowcount(sql: str, params: tuple = ()) -> int:
    return sqlitestore.executar_retornando_rowcount(conectar, sql, params)


def um(sql: str, params: tuple = ()):
    return sqlitestore.consultar_um(conectar, sql, params)


def todos(sql: str, params: tuple = ()) -> list:
    return sqlitestore.consultar_todos(conectar, sql, params)
