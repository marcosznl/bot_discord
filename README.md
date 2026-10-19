# bot_discord

Bot de Discord em Python ([discord.py](https://github.com/Rapptz/discord.py) 2.7) com moderação, comunidade, níveis (XP), música e diversão. Só usa slash commands e menus de clique direito, e os painéis usam o layout novo do Discord (Components V2).

## Comandos

### Geral
| Comando | O que faz |
| --- | --- |
| `/ping` | Mostra se o bot está online e a latência |
| `/ajuda` | Lista todos os comandos por categoria |
| `/usuario [usuario]` | Cartão de perfil: datas de criação e entrada, cargos, banner |
| `/avatar [usuario]` | Avatar em tamanho grande (com o do servidor, se houver) e links para baixar |
| `/servidor` | Informações do servidor: dono, membros, canais, impulsos, banner |

### Moderação
| Comando | O que faz |
| --- | --- |
| `/mod silenciar` | Castigo do Discord por 1 min a 28 dias |
| `/mod dessilenciar` | Tira o castigo |
| `/mod expulsar` | Expulsa e avisa a pessoa por DM |
| `/mod banir` | Bane (também por ID, mesmo de quem já saiu) e pode apagar mensagens recentes |
| `/mod desbanir` | Desbane, com a lista de banidos no autocomplete |
| `/mod advertir` | Registra uma advertência e avisa a pessoa por DM |
| `/mod advertencias` | Mostra o histórico de advertências de alguém |
| `/mod perdoar` | Apaga as advertências de alguém |
| `/mod limpar` | Apaga até 200 mensagens, com filtro opcional por pessoa |
| `/mod lento` | Liga ou desliga o modo lento do canal |
| `/mod trancar` / `/mod destrancar` | Impede ou libera mensagens de quem não tem cargo especial |
| `/logs` | Escolhe o canal que recebe os logs da moderação |
| `/automod` | Painel para ligar as regras nativas do AutoMod (palavrões, spam, spam de menções, palavras proibidas) |
| `/boasvindas` | Canal e texto de boas-vindas para novos membros |

O canal de `/logs` recebe: mensagens apagadas e editadas, entradas e saídas, banimentos, expulsões e castigos (vindos do bot ou de qualquer outro lugar, com quem fez e o motivo), advertências, denúncias e alertas do AutoMod.

Hierarquia: ninguém consegue moderar alguém com cargo igual ou acima do seu (ou do bot), nem o dono do servidor.

### Comunidade
| Comando | O que faz |
| --- | --- |
| `/enquete` | Enquete nativa do Discord, com 2 a 10 opções, duração e opção de múltipla escolha |
| `/cargos` | Publica um painel com até 20 botões de cargo: clica para pegar, clica de novo para largar |
| `/tickets` | Publica um painel com um botão que abre uma thread privada entre a pessoa e a equipe |
| `/evento` | Cria um evento agendado (em canal de voz/palco ou externo), com capa opcional |
| `/lembrete criar` | Lembrete por tempo (`10m`, `1h30m`, `2d`) ou data (`25/12 20:00`, `amanha 9h`) |
| `/lembrete listar` | Mostra seus lembretes pendentes |
| `/lembrete cancelar` | Cancela um lembrete |

Os botões dos painéis de cargos e tickets continuam funcionando depois que o bot reinicia. Cargos com permissão de moderação ou administração não podem ir para o painel. Os lembretes ficam salvos em disco.

### Níveis
| Comando | O que faz |
| --- | --- |
| `/xp` | Menu com seu rank, o ranking do servidor, como funciona e as configurações (admin) |

A cada mensagem a pessoa ganha de 15 a 25 XP (no máximo uma vez a cada 20 s). Quem tem Gerenciar Servidor pode ligar ou desligar o sistema, zerar o XP do servidor, dar ou tirar XP e configurar cargos automáticos por nível.

### Diversão
| Comando | O que faz |
| --- | --- |
| `/interagir` | 25 interações com gif de anime (abraçar, beijar, cafuné, toca aqui, socão, guerra de travesseiro…). Quem recebe pode clicar em **Retribuir** |
| `/gif` | Busca um gif no Giphy |

### Música
| Comando | O que faz |
| --- | --- |
| `/musica` | Painel "tocando agora" com capa, barra de progresso e as próximas da fila. Botões: tocar (link ou busca no YouTube), anterior, pular, parar, ver fila, limpar fila e atualizar |

O bot sai sozinho do canal de voz depois de 5 minutos sem tocar nada.

### Menus de clique direito (Apps)
- **Em uma pessoa:** Ver perfil, Ver avatar, Ver rank XP
- **Em uma mensagem:** Denunciar mensagem (vai para o canal de logs), Fixar ou desafixar (precisa de Gerenciar Mensagens)

`/ping`, `/ajuda`, `/usuario`, `/avatar`, `/gif`, `/enquete`, `/lembrete` e os menus Ver perfil e Ver avatar também funcionam em DM e se o bot for instalado na sua conta ("Adicionar aos meus apps").

## Instalação

Requer Python 3.10 ou mais novo e o `ffmpeg` no PATH (para música).

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Crie um `.env` na raiz:

```
DISCORD_TOKEN=token-do-bot
GIPHY_API_KEY=chave-da-api-do-giphy
# Opcional, padrão America/Sao_Paulo - fuso das datas digitadas em /evento e /lembrete
FUSO_HORARIO=America/Sao_Paulo
```

No [Developer Portal](https://discord.com/developers/applications), em **Bot > Privileged Gateway Intents**, ligue **Server Members Intent** e **Message Content Intent**. Sem isso o bot não conecta.

Para convidar o bot com todas as permissões que ele usa:

```
https://discord.com/oauth2/authorize?client_id=SEU_CLIENT_ID&scope=bot+applications.commands&permissions=19061336829110
```

O cargo do bot precisa ficar **acima** dos cargos que ele vai dar (cargos por nível, painel de cargos) e das pessoas que ele vai moderar.

Para rodar:

```bash
venv/bin/python main.py
```

## Permissões dos comandos

Os comandos de administração já vêm escondidos para quem não tem a permissão correspondente: `/mod` (Moderar Membros), `/logs`, `/automod`, `/boasvindas` e `/tickets` (Gerenciar Servidor), `/cargos` (Gerenciar Cargos) e `/evento` (Gerenciar Eventos). Para mudar quem vê cada um, use **Configurações do servidor > Integrações > nome do bot**. Cada subcomando de `/mod` ainda confere a permissão específica dele (por exemplo, Banir Membros no `/mod banir`).

## Dados

Tudo fica em `data/` (fora do git): `xp.db`, `moderacao.db` (advertências e canal de logs) e `comunidade.db` (lembretes). A configuração de boas-vindas fica em `boas_vindas.json`. As regras do AutoMod ficam no próprio Discord, com o prefixo `[Bot]`.

## Testes

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/pytest
```

Além das funções puras, os testes carregam o bot inteiro sem conectar e serializam todos os comandos e painéis. Isso pega nome inválido, limite de componentes e erro de import antes de subir o bot de verdade.
