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
