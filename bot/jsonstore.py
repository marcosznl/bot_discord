import json
import logging
import os

logger = logging.getLogger(__name__)


def ler_json(caminho: str) -> dict:
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error(f"Arquivo JSON corrompido em {caminho} - tratando como vazio.")
        return {}


def escrever_json(caminho: str, dados: dict) -> None:
    # Escreve num arquivo temporario e so troca pelo definitivo no final
    # (os.replace e atomico) - assim uma queda do processo no meio da escrita
    # nao deixa o arquivo original truncado/corrompido.
    caminho_temporario = f"{caminho}.tmp"
    with open(caminho_temporario, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=2)
    os.replace(caminho_temporario, caminho)
