import asyncio

import psutil

# Margem de seguranca somada a qualquer estimativa de RAM necessaria, para
# sobrar espaco para o sistema operacional e o proprio bot. Usado por
# minecraft.py (ligar servidor) e llm.py (carregar modelo).
RAM_MARGEM_SEGURANCA_GB = 0.5


def _ram_disponivel_gb() -> float:
    return psutil.virtual_memory().available / (1024**3)


def _ram_suficiente_para(necessaria_gb: float) -> tuple[bool, float]:
    disponivel = _ram_disponivel_gb()
    return disponivel >= necessaria_gb + RAM_MARGEM_SEGURANCA_GB, disponivel


async def _ram_verificar_ou_mensagem(necessaria_gb: float, contexto: str) -> str | None:
    """Confere se ha RAM suficiente; devolve None se sim, ou uma mensagem de
    erro pronta para enviar ao usuario se nao ("contexto" entra na frase:
    ex. "ligar {nome}", "carregar `{modelo}` agora")."""
    suficiente, disponivel = await asyncio.to_thread(_ram_suficiente_para, necessaria_gb)
    if suficiente:
        return None
    return (
        f"Nao ha RAM suficiente para {contexto} "
        f"(precisa de ~{necessaria_gb:.1f}GB, tem {disponivel:.1f}GB disponivel)."
    )
