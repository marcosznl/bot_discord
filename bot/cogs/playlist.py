import asyncio
import os
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import DATA_DIR
from bot.cogs.musica import (
    ModalVolumeMusica,
    _filas_musica,
    _formatar_duracao_musica,
    _musica_acao_anterior,
    _musica_acao_continuar,
    _musica_acao_fila,
    _musica_acao_pausar,
    _musica_acao_pular,
    _musica_acao_parar,
    _tocar_proximo,
    _ytdlp_buscar,
)

PLAYLIST_DB_PATH = os.path.join(DATA_DIR, "playlists.db")


def _playlist_conectar_bd():
    conexao = sqlite3.connect(PLAYLIST_DB_PATH)
    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS playlist_musicas (
            user_id INTEGER NOT NULL,
            playlist TEXT NOT NULL,
            titulo TEXT NOT NULL,
            busca TEXT NOT NULL
        )
        """
    )
    colunas = [linha[1] for linha in conexao.execute("PRAGMA table_info(playlist_musicas)")]
    if "duracao" not in colunas:
        conexao.execute("ALTER TABLE playlist_musicas ADD COLUMN duracao INTEGER")
    return conexao


def _playlist_adicionar(user_id: int, playlist: str, titulo: str, busca: str, duracao: int | None) -> None:
    with _playlist_conectar_bd() as conexao:
        conexao.execute(
            "INSERT INTO playlist_musicas (user_id, playlist, titulo, busca, duracao) VALUES (?, ?, ?, ?, ?)",
            (user_id, playlist, titulo, busca, duracao),
        )
        conexao.commit()


def _playlist_listar(user_id: int) -> list[tuple[str, int]]:
    with _playlist_conectar_bd() as conexao:
        cursor = conexao.execute(
            "SELECT playlist, COUNT(*) FROM playlist_musicas WHERE user_id = ? GROUP BY playlist ORDER BY playlist",
            (user_id,),
        )
        return cursor.fetchall()


def _playlist_musicas(user_id: int, playlist: str) -> list[tuple[int, str, str, int | None]]:
    with _playlist_conectar_bd() as conexao:
        cursor = conexao.execute(
            "SELECT rowid, titulo, busca, duracao FROM playlist_musicas WHERE user_id = ? AND playlist = ?",
            (user_id, playlist),
        )
        return cursor.fetchall()


def _playlist_remover_musica(rowid: int) -> None:
    with _playlist_conectar_bd() as conexao:
        conexao.execute("DELETE FROM playlist_musicas WHERE rowid = ?", (rowid,))
        conexao.commit()


def _playlist_apagar(user_id: int, playlist: str) -> int:
    with _playlist_conectar_bd() as conexao:
        cursor = conexao.execute(
            "DELETE FROM playlist_musicas WHERE user_id = ? AND playlist = ?",
            (user_id, playlist),
        )
        conexao.commit()
        return cursor.rowcount


async def _playlist_acao_adicionar(interaction: discord.Interaction, nome: str, busca: str) -> None:
    await interaction.response.defer(ephemeral=True)

    try:
        titulo, _, _, duracao = await asyncio.to_thread(_ytdlp_buscar, busca)
    except Exception as erro:
        print(f"Erro ao buscar musica '{busca}' para playlist: {erro}")
        await interaction.followup.send("Nao consegui encontrar essa musica.")
        return

    await asyncio.to_thread(_playlist_adicionar, interaction.user.id, nome, titulo, busca, duracao)
    await interaction.followup.send(f"Adicionado **{titulo}** ({_formatar_duracao_musica(duracao)}) na playlist **{nome}**.")


async def _playlist_acao_listar(interaction: discord.Interaction) -> None:
    playlists = await asyncio.to_thread(_playlist_listar, interaction.user.id)
    if not playlists:
        await interaction.response.send_message("Voce ainda nao tem nenhuma playlist.", ephemeral=True)
        return

    linhas = [f"**{nome}** - {quantidade} musica(s)" for nome, quantidade in playlists]
    embed = discord.Embed(
        title=f"Playlists de {interaction.user.display_name}", description="\n".join(linhas), color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _playlist_acao_tocar(interaction: discord.Interaction, nome: str) -> None:
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message("Voce precisa estar em um canal de voz.", ephemeral=True)
        return

    await interaction.response.defer()

    musicas = await asyncio.to_thread(_playlist_musicas, interaction.user.id, nome)
    if not musicas:
        await interaction.followup.send(f"A playlist **{nome}** esta vazia.")
        return

    canal_voz = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await canal_voz.connect()
    elif voice_client.channel != canal_voz:
        await voice_client.move_to(canal_voz)

    fila = _filas_musica.setdefault(interaction.guild.id, [])
    fila.extend((titulo, busca, duracao) for _, titulo, busca, duracao in musicas)

    if not voice_client.is_playing() and not voice_client.is_paused():
        await asyncio.to_thread(_tocar_proximo, interaction.guild.id, voice_client)

    linhas = [
        f"{posicao}. {titulo} ({_formatar_duracao_musica(duracao)})"
        for posicao, (_, titulo, _, duracao) in enumerate(musicas, start=1)
    ]
    embed = discord.Embed(
        title=f"Playlist {nome} adicionada na fila",
        description="\n".join(linhas),
        color=discord.Color.blurple(),
    )
    await interaction.followup.send(embed=embed)


async def _playlist_acao_apagar(interaction: discord.Interaction, nome: str) -> None:
    removidos = await asyncio.to_thread(_playlist_apagar, interaction.user.id, nome)
    if removidos:
        await interaction.response.send_message(f"Playlist **{nome}** apagada ({removidos} musica(s)).", ephemeral=True)
    else:
        await interaction.response.send_message(f"Playlist **{nome}** nao encontrada.", ephemeral=True)


class ModalAdicionarPlaylist(discord.ui.Modal, title="Nova playlist"):
    nome = discord.ui.TextInput(label="Nome da playlist", max_length=50)
    busca = discord.ui.TextInput(label="Link do YouTube ou busca", max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        await _playlist_acao_adicionar(interaction, str(self.nome).strip(), str(self.busca))


class ModalAdicionarMusicaComPlaylist(discord.ui.Modal, title="Adicionar musica"):
    busca = discord.ui.TextInput(label="Link do YouTube ou busca", max_length=200)

    def __init__(self, nome_playlist: str):
        super().__init__()
        self.nome_playlist = nome_playlist

    async def on_submit(self, interaction: discord.Interaction):
        await _playlist_acao_adicionar(interaction, self.nome_playlist, str(self.busca))


class SelecionarPlaylistAdicionar(discord.ui.View):
    NOVA_PLAYLIST = "__nova__"

    def __init__(self, playlists: list[str]):
        super().__init__(timeout=600)
        opcoes = [discord.SelectOption(label=nome, value=nome) for nome in playlists[:24]]
        opcoes.append(discord.SelectOption(label="+ Criar nova playlist", value=self.NOVA_PLAYLIST))
        select = discord.ui.Select(placeholder="Em qual playlist adicionar?", options=opcoes)
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        valor = interaction.data["values"][0]
        if valor == self.NOVA_PLAYLIST:
            await interaction.response.send_modal(ModalAdicionarPlaylist())
        else:
            await interaction.response.send_modal(ModalAdicionarMusicaComPlaylist(valor))


class SelecionarPlaylistTocar(discord.ui.View):
    def __init__(self, playlists: list[str]):
        super().__init__(timeout=600)
        select = discord.ui.Select(
            placeholder="Escolha a playlist",
            options=[discord.SelectOption(label=nome) for nome in playlists[:25]],
        )
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        nome = interaction.data["values"][0]
        await _playlist_acao_tocar(interaction, nome)


class SelecionarPlaylistApagar(discord.ui.View):
    def __init__(self, playlists: list[str]):
        super().__init__(timeout=600)
        select = discord.ui.Select(
            placeholder="Escolha a playlist para apagar",
            options=[discord.SelectOption(label=nome) for nome in playlists[:25]],
        )
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        nome = interaction.data["values"][0]
        await _playlist_acao_apagar(interaction, nome)


class SelecionarMusicaEditar(discord.ui.View):
    def __init__(self, playlist: str, musicas: list[tuple[int, str, str, int | None]]):
        super().__init__(timeout=600)
        self.playlist = playlist
        select = discord.ui.Select(
            placeholder="Escolha a musica para remover",
            options=[
                discord.SelectOption(label=f"{titulo[:80]} ({_formatar_duracao_musica(duracao)})", value=str(rowid))
                for rowid, titulo, _, duracao in musicas[:25]
            ],
        )
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        rowid = int(interaction.data["values"][0])
        await asyncio.to_thread(_playlist_remover_musica, rowid)
        await interaction.response.edit_message(content=f"Musica removida da playlist **{self.playlist}**.", view=None)


class BotoesEditarPlaylist(discord.ui.View):
    def __init__(self, nome: str):
        super().__init__(timeout=600)
        self.nome = nome

    @discord.ui.button(label="Adicionar musica", style=discord.ButtonStyle.success)
    async def adicionar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalAdicionarMusicaComPlaylist(self.nome))

    @discord.ui.button(label="Remover musica", style=discord.ButtonStyle.danger)
    async def remover(self, interaction: discord.Interaction, botao: discord.ui.Button):
        musicas = await asyncio.to_thread(_playlist_musicas, interaction.user.id, self.nome)
        if not musicas:
            await interaction.response.send_message(f"A playlist **{self.nome}** esta vazia.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Qual musica remover da playlist **{self.nome}**?",
            view=SelecionarMusicaEditar(self.nome, musicas),
            ephemeral=True,
        )


class SelecionarPlaylistEditar(discord.ui.View):
    def __init__(self, playlists: list[str]):
        super().__init__(timeout=600)
        select = discord.ui.Select(
            placeholder="Escolha a playlist para editar",
            options=[discord.SelectOption(label=nome) for nome in playlists[:25]],
        )
        select.callback = self._escolhido
        self.add_item(select)

    async def _escolhido(self, interaction: discord.Interaction):
        nome = interaction.data["values"][0]
        await interaction.response.edit_message(
            content=f"O que voce quer fazer com a playlist **{nome}**?", view=BotoesEditarPlaylist(nome)
        )


class BotoesPlaylist(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Adicionar musica", style=discord.ButtonStyle.success)
    async def adicionar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        playlists = await asyncio.to_thread(_playlist_listar, interaction.user.id)
        if not playlists:
            await interaction.response.send_modal(ModalAdicionarPlaylist())
            return
        nomes = [nome for nome, _ in playlists]
        await interaction.response.send_message(
            "Em qual playlist adicionar?", view=SelecionarPlaylistAdicionar(nomes), ephemeral=True
        )

    @discord.ui.button(label="Minhas playlists", style=discord.ButtonStyle.secondary)
    async def listar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _playlist_acao_listar(interaction)

    @discord.ui.button(label="Tocar playlist", style=discord.ButtonStyle.primary)
    async def tocar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        playlists = await asyncio.to_thread(_playlist_listar, interaction.user.id)
        if not playlists:
            await interaction.response.send_message("Voce ainda nao tem nenhuma playlist.", ephemeral=True)
            return
        nomes = [nome for nome, _ in playlists]
        await interaction.response.send_message(
            "Escolha a playlist para tocar:", view=SelecionarPlaylistTocar(nomes), ephemeral=True
        )

    @discord.ui.button(label="Apagar playlist", style=discord.ButtonStyle.danger)
    async def apagar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        playlists = await asyncio.to_thread(_playlist_listar, interaction.user.id)
        if not playlists:
            await interaction.response.send_message("Voce ainda nao tem nenhuma playlist.", ephemeral=True)
            return
        nomes = [nome for nome, _ in playlists]
        await interaction.response.send_message(
            "Escolha a playlist para apagar:", view=SelecionarPlaylistApagar(nomes), ephemeral=True
        )

    @discord.ui.button(label="Editar playlist", style=discord.ButtonStyle.secondary)
    async def editar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        playlists = await asyncio.to_thread(_playlist_listar, interaction.user.id)
        if not playlists:
            await interaction.response.send_message("Voce ainda nao tem nenhuma playlist.", ephemeral=True)
            return
        nomes = [nome for nome, _ in playlists]
        await interaction.response.send_message(
            "Escolha a playlist para editar:", view=SelecionarPlaylistEditar(nomes), ephemeral=True
        )

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_anterior(interaction)

    @discord.ui.button(label="Pular", style=discord.ButtonStyle.secondary)
    async def pular(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_pular(interaction)

    @discord.ui.button(label="Parar", style=discord.ButtonStyle.danger)
    async def parar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_parar(interaction)

    @discord.ui.button(label="Volume", style=discord.ButtonStyle.primary)
    async def volume(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await interaction.response.send_modal(ModalVolumeMusica())

    @discord.ui.button(label="Fila", style=discord.ButtonStyle.secondary)
    async def fila(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_fila(interaction)

    @discord.ui.button(label="Pausar", style=discord.ButtonStyle.secondary)
    async def pausar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_pausar(interaction)

    @discord.ui.button(label="Continuar", style=discord.ButtonStyle.success)
    async def continuar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        await _musica_acao_continuar(interaction)


class Playlist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="playlist", description="Crie, toque, edite e apague suas playlists de musica salvas")
    async def playlist(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "O que voce quer fazer com suas playlists?", view=BotoesPlaylist(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Playlist(bot))
