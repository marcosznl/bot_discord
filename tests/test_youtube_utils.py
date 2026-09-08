import asyncio

import bot.cogs.youtube as youtube
from bot.cogs.youtube import _youtube_extrair_channel_id, _youtube_parsear_feed


class _RespostaFalsa:
    def __init__(self, status, texto=""):
        self.status = status
        self._texto = texto

    async def text(self):
        return self._texto

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _SessaoFalsa:
    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def get(self, url, headers=None):
        resposta = self._respostas[self.chamadas]
        self.chamadas += 1
        return resposta

FEED_EXEMPLO = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <title>Canal de Exemplo</title>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>Video mais recente</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <media:group>
      <media:thumbnail url="https://i3.ytimg.com/vi/abc123/hqdefault.jpg" width="480" height="360"/>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>xyz789</yt:videoId>
    <title>Video mais antigo</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=xyz789"/>
  </entry>
</feed>
"""


def test_parsear_feed_extrai_nome_e_videos_em_ordem():
    nome_canal, videos = _youtube_parsear_feed(FEED_EXEMPLO)

    assert nome_canal == "Canal de Exemplo"
    assert [video["video_id"] for video in videos] == ["abc123", "xyz789"]
    assert videos[0]["titulo"] == "Video mais recente"
    assert videos[0]["link"] == "https://www.youtube.com/watch?v=abc123"


def test_parsear_feed_usa_thumbnail_do_feed_quando_disponivel():
    _, videos = _youtube_parsear_feed(FEED_EXEMPLO)
    assert videos[0]["thumbnail"] == "https://i3.ytimg.com/vi/abc123/hqdefault.jpg"


def test_parsear_feed_usa_thumbnail_padrao_quando_ausente():
    _, videos = _youtube_parsear_feed(FEED_EXEMPLO)
    assert videos[1]["thumbnail"] == "https://i.ytimg.com/vi/xyz789/hqdefault.jpg"


def test_parsear_feed_xml_invalido_devolve_none():
    assert _youtube_parsear_feed("isso nao e xml") is None


def test_extrair_channel_id_usa_link_canonico():
    html = (
        "<head>"
        '<link rel="canonical" href="https://www.youtube.com/channel/UC1234567890123456789012">'
        "</head>"
    )
    assert _youtube_extrair_channel_id(html) == "UC1234567890123456789012"


def test_extrair_channel_id_ignora_ids_soltos_no_html():
    # IDs de canais recomendados/relacionados espalhados pela pagina nao devem
    # ser confundidos com o canal da propria pagina.
    html = (
        'href="https://www.youtube.com/channel/UC0000000000000000000000" '
        '<meta property="og:url" content="https://www.youtube.com/channel/UC1234567890123456789012">'
    )
    assert _youtube_extrair_channel_id(html) == "UC1234567890123456789012"


def test_extrair_channel_id_ausente_devolve_none():
    assert _youtube_extrair_channel_id("<html>sem nada aqui</html>") is None


def test_opcoes_pasta_inclui_pasta_principal_subpastas_e_criar_nova():
    opcoes = youtube._youtube_opcoes_pasta(["marcos", "victor"])
    assert [o.value for o in opcoes] == ["_raiz", "marcos", "victor", "_nova"]
    assert opcoes[0].label == "Pasta principal (sem subpasta)"


def test_nome_pasta_valido_aceita_nome_simples():
    assert youtube._youtube_nome_pasta_valido("familia") is True


def test_nome_pasta_valido_rejeita_vazio_e_pontos():
    assert youtube._youtube_nome_pasta_valido("") is False
    assert youtube._youtube_nome_pasta_valido(".") is False
    assert youtube._youtube_nome_pasta_valido("..") is False


def test_nome_pasta_valido_rejeita_separador_de_caminho():
    assert youtube._youtube_nome_pasta_valido("a/b") is False
    assert youtube._youtube_nome_pasta_valido("a\\b") is False


def test_pastas_disponiveis_lista_apenas_subpastas(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    (tmp_path / "victor").mkdir()
    (tmp_path / "marcos").mkdir()
    (tmp_path / ".oculta").mkdir()
    (tmp_path / "arquivo.txt").write_text("x")

    assert youtube._youtube_pastas_disponiveis() == ["marcos", "victor"]


def test_pastas_disponiveis_pasta_download_inexistente_devolve_vazio(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path / "nao-existe"))
    assert youtube._youtube_pastas_disponiveis() == []


def test_pasta_destino_raiz_quando_sem_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    assert youtube._youtube_pasta_destino("") == str(tmp_path)


def test_pasta_destino_cria_subpasta_se_nao_existir(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    destino = youtube._youtube_pasta_destino("victor")
    assert destino == str(tmp_path / "victor")
    assert (tmp_path / "victor").is_dir()




def test_get_com_retry_sucesso_de_primeira():
    sessao = _SessaoFalsa([_RespostaFalsa(200, "ok")])
    status, texto = asyncio.run(youtube._youtube_get_com_retry(sessao, "http://x"))
    assert (status, texto) == (200, "ok")
    assert sessao.chamadas == 1


def test_get_com_retry_falha_passageira_depois_sucesso(monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_HTTP_ESPERA_SEGUNDOS", 0)
    sessao = _SessaoFalsa([_RespostaFalsa(404), _RespostaFalsa(500), _RespostaFalsa(200, "ok")])
    status, texto = asyncio.run(youtube._youtube_get_com_retry(sessao, "http://x"))
    assert (status, texto) == (200, "ok")
    assert sessao.chamadas == 3


def test_get_com_retry_esgota_tentativas_devolve_ultimo_status(monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_HTTP_ESPERA_SEGUNDOS", 0)
    sessao = _SessaoFalsa([_RespostaFalsa(404), _RespostaFalsa(404), _RespostaFalsa(500)])
    status, texto = asyncio.run(youtube._youtube_get_com_retry(sessao, "http://x"))
    assert texto is None
    assert status == 500
    assert sessao.chamadas == 3
