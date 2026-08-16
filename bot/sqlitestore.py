import sqlite3
from typing import Callable

Conectar = Callable[[], sqlite3.Connection]


def executar(conectar: Conectar, sql: str, params: tuple = ()) -> None:
    with conectar() as conexao:
        conexao.execute(sql, params)
        conexao.commit()


def executar_retornando_rowcount(conectar: Conectar, sql: str, params: tuple = ()) -> int:
    with conectar() as conexao:
        cursor = conexao.execute(sql, params)
        conexao.commit()
        return cursor.rowcount


def consultar_um(conectar: Conectar, sql: str, params: tuple = ()):
    with conectar() as conexao:
        return conexao.execute(sql, params).fetchone()


def consultar_todos(conectar: Conectar, sql: str, params: tuple = ()) -> list:
    with conectar() as conexao:
        return conexao.execute(sql, params).fetchall()
