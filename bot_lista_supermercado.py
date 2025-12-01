import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Optional

from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============== CONFIGURAÇÃO BÁSICA ===============
load_dotenv()  # Carrega TELEGRAM_BOT_TOKEN e OPENAI_API_KEY do .env

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# =============== MODELO DE DADOS ===============

@dataclass
class ItemLista:
    nome: str
    em_carrinho: bool = False
    preco: Optional[float] = None  # preço na prateleira (se já estiver no carrinho)


@dataclass
class EstadoUsuario:
    itens: Dict[str, ItemLista] = field(default_factory=dict)
    modo_compras: bool = False  # True quando usuário estiver "fazendo compras"


# =============== FUNÇÃO DE TRANSCRIÇÃO DE ÁUDIO ===============

def transcribe_voice(file_path: str) -> str:
    """
    Recebe o caminho do arquivo de áudio e devolve o texto reconhecido.
    Aqui usamos a API Whisper da OpenAI apenas como EXEMPLO.
    Você pode trocar essa implementação por outro serviço de STT.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Sem API key configurada, você pode levantar erro
        # ou retornar uma string "vazia"/mensagem padrão.
        raise RuntimeError(
            "OPENAI_API_KEY não definida. Configure para habilitar comandos de voz."
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "Biblioteca 'openai' não instalada. Rode 'pip install openai'."
        ) from e

    client = OpenAI(api_key=api_key)

    with open(file_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="pt",  # força reconhecimento em português
        )
    # O objeto retornado normalmente possui o texto em transcription.text
    return transcription.text


# =============== FUNÇÕES DE AJUDA PARA ESTADO ===============

def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> EstadoUsuario:
    """
    Recupera o estado do usuário do contexto do Telegram.
    """
    if "estado" not in context.user_data:
        context.user_data["estado"] = EstadoUsuario()
    return context.user_data["estado"]


def adicionar_item(estado: EstadoUsuario, nome_item: str) -> str:
    nome = nome_item.strip().lower()
    if not nome:
        return "Você precisa informar o nome do item para adicionar."

    if nome in estado.itens:
        return f"'{nome_item}' já está na lista."

    estado.itens[nome] = ItemLista(nome=nome)
    return f"Item '{nome_item}' adicionado à lista."


def remover_item(estado: EstadoUsuario, nome_item: str) -> str:
    nome = nome_item.strip().lower()
    if not nome:
        return "Você precisa informar o nome do item para remover."

    if nome not in estado.itens:
        return f"Item '{nome_item}' não está na lista."

    del estado.itens[nome]
    return f"Item '{nome_item}' removido da lista."


def listar_itens(estado: EstadoUsuario) -> str:
    if not estado.itens:
        return "Sua lista está vazia."

    pendentes = []
    comprados = []
    total = 0.0

    for item in estado.itens.values():
        if item.em_carrinho:
            preco_txt = f"R$ {item.preco:.2f}" if item.preco is not None else "-"
            comprados.append(f"- {item.nome} ({preco_txt})")
            if item.preco is not None:
                total += item.preco
        else:
            pendentes.append(f"- {item.nome}")

    msg = []

    msg.append("📝 *Lista de supermercado*")
    msg.append("")

    msg.append("📌 *Pendentes:*")
    if pendentes:
        msg.extend(pendentes)
    else:
        msg.append("- Nenhum item pendente.")
    msg.append("")

    msg.append("🛒 *No carrinho:*")
    if comprados:
        msg.extend(comprados)
        msg.append("")
        msg.append(f"💰 *Total parcial:* R$ {total:.2f}")
    else:
        msg.append("- Nenhum item ainda foi marcado como comprado.")

    return "\n".join(msg)


def marcar_item_comprado(estado: EstadoUsuario, texto: str) -> str:
    """
    Em modo compras: extrai item e preço do texto.
    Ex: "peguei leite por 4.50" ou "peguei leite 4,50"
    """
    # Primeiro, achar um número (preço) no texto
    # Padrão: último número no texto, tipo 4,50 / 4.50 / 10
    match = re.search(r"(\d+[.,]\d+|\d+)\s*$", texto)
    if not match:
        return (
            "Não encontrei o preço na mensagem. "
            "Exemplo: 'peguei leite por 4.50'."
        )

    preco_str = match.group(1).replace(",", ".")
    try:
        preco = float(preco_str)
    except ValueError:
        return "Não consegui converter o preço. Tente dizer algo como '4.50' ou '4,50'."

    # Texto sem o preço
    texto_sem_preco = texto[: match.start()].strip().lower()

    # Remover palavras comuns de ação
    for palavra in ["peguei", "peguei o", "peguei a", "marcar", "marquei", "coloquei", "coloquei o", "coloquei a"]:
        if texto_sem_preco.startswith(palavra):
            texto_sem_preco = texto_sem_preco[len(palavra) :].strip()
            break

    nome_item = texto_sem_preco.strip()
    if not nome_item:
        return (
            "Não entendi qual item você pegou. "
            "Exemplo: 'peguei leite por 4.50'."
        )

    nome_chave = nome_item.lower()
    if nome_chave not in estado.itens:
        # Se o item não existir, vamos adicionar direto já como comprado
        estado.itens[nome_chave] = ItemLista(
            nome=nome_item, em_carrinho=True, preco=preco
        )
        msg_base = f"Item '{nome_item}' não estava na lista, mas foi adicionado"
    else:
        item = estado.itens[nome_chave]
        item.em_carrinho = True
        item.preco = preco
        msg_base = f"Item '{nome_item}' marcado como no carrinho"

    # Calcular total
    total = 0.0
    for item in estado.itens.values():
        if item.em_carrinho and item.preco is not None:
            total += item.preco

    return f"{msg_base} com preço R$ {preco:.2f}.\n💰 Total parcial: R$ {total:.2f}"


# =============== PARSER DE COMANDOS EM LINGUAGEM NATURAL ===============

async def processar_texto_natural(
    update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str
) -> None:
    """
    Processa comandos em português tanto vindos de mensagens de texto
    quanto de voz (transcritas).
    """
    if not texto:
        await update.message.reply_text("Não consegui entender a mensagem.")
        return

    estado = get_user_state(context)
    texto_original = texto.strip()
    texto_lower = texto_original.lower()

    # 1) Comandos de controle / modo
    if "fazendo compras" in texto_lower or "modo compras" in texto_lower:
        estado.modo_compras = True
        await update.message.reply_text(
            "🛒 Modo *fazendo compras* ativado!\n\n"
            "Agora você pode dizer, por exemplo:\n"
            "- 'peguei leite por 4.50'\n"
            "- 'peguei arroz 10,00'\n\n"
            "Vou marcar o item como colocado no carrinho e somar o valor."
        )
        return

    if "sair do modo compras" in texto_lower or "encerrar compras" in texto_lower:
        estado.modo_compras = False
        await update.message.reply_text(
            "Modo *fazendo compras* desativado. Você pode voltar a gerenciar a lista normalmente."
        )
        return

    # 2) Se estiver em modo compras, priorizamos marcar itens no carrinho
    if estado.modo_compras:
        if any(texto_lower.startswith(pref) for pref in ["peguei", "marcar", "marquei", "coloquei"]):
            msg = marcar_item_comprado(estado, texto_original)
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

    # 3) Adicionar item (sem barra)
    if texto_lower.startswith("adicionar ") or texto_lower.startswith("adiciona "):
        partes = texto_original.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("Diga algo como: 'adicionar leite'.")
            return
        nome_item = partes[1]
        msg = adicionar_item(estado, nome_item)
        await update.message.reply_text(msg)
        return

    # 4) Remover item (sem barra)
    if texto_lower.startswith("remover ") or texto_lower.startswith("remove ") or texto_lower.startswith("tirar "):
        partes = texto_original.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("Diga algo como: 'remover leite'.")
            return
        nome_item = partes[1]
        msg = remover_item(estado, nome_item)
        await update.message.reply_text(msg)
        return

    # 5) Listar itens
    if "listar" in texto_lower or "mostrar lista" in texto_lower or "ver lista" in texto_lower:
        msg = listar_itens(estado)
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # 6) Se não reconheceu nada:
    await update.message.reply_text(
        "Não entendi o comando.\n"
        "Você pode tentar algo como:\n"
        "- 'adicionar leite'\n"
        "- 'remover arroz'\n"
        "- 'mostrar lista'\n"
        "- 'fazendo compras'\n"
        "- 'peguei leite por 4.50' (em modo compras)"
    )


# =============== HANDLERS DE COMANDO (texto com /) ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    get_user_state(context)  # garante que o estado exista
    await update.message.reply_text(
        "👋 Olá! Eu sou o *Bot Lista de Supermercado*.\n\n"
        "Posso criar e gerenciar sua lista por *texto* ou *voz*.\n\n"
        "*Comandos básicos:*\n"
        "- /add <item> — adiciona um item\n"
        "- /remove <item> — remove um item\n"
        "- /list — mostra sua lista\n"
        "- /compras — entra no modo 'fazendo compras'\n\n"
        "Também funciona em linguagem natural. Ex:\n"
        "- 'adicionar leite'\n"
        "- 'mostrar lista'\n"
        "- 'fazendo compras'\n"
        "- 'peguei arroz por 10,00'\n",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = get_user_state(context)
    if not context.args:
        await update.message.reply_text("Use: /add nome do item")
        return
    nome_item = " ".join(context.args)
    msg = adicionar_item(estado, nome_item)
    await update.message.reply_text(msg)


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = get_user_state(context)
    if not context.args:
        await update.message.reply_text("Use: /remove nome do item")
        return
    nome_item = " ".join(context.args)
    msg = remover_item(estado, nome_item)
    await update.message.reply_text(msg)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = get_user_state(context)
    msg = listar_itens(estado)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def compras_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = get_user_state(context)
    estado.modo_compras = True
    await update.message.reply_text(
        "🛒 Modo *fazendo compras* ativado!\n\n"
        "Agora você pode mandar mensagens (texto ou voz) como:\n"
        "- 'peguei leite por 4.50'\n"
        "- 'peguei arroz 10,00'\n\n"
        "Eu marco o item como no carrinho e somo o valor.",
        parse_mode="Markdown",
    )


# =============== HANDLERS DE TEXTO E VOZ ===============

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = update.message.text or ""
    await processar_texto_natural(update, context, texto)


async def voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Recebe uma mensagem de voz/áudio, faz o download,
    transcreve para texto e passa para o mesmo parser de texto.
    """
    if not (update.message.voice or update.message.audio):
        await update.message.reply_text("Não encontrei áudio na mensagem.")
        return

    file = None
    if update.message.voice:
        file = await update.message.voice.get_file()
    elif update.message.audio:
        file = await update.message.audio.get_file()

    if not file:
        await update.message.reply_text("Não consegui obter o arquivo de áudio.")
        return

    tmp_file = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()

    try:
        await file.download_to_drive(tmp_path)
        try:
            texto = transcribe_voice(tmp_path)
        except Exception as e:
            logger.exception("Erro na transcrição de áudio")
            await update.message.reply_text(
                "Não consegui transcrever o áudio. "
                "Verifique a configuração de STT (voz)."
            )
            return

        await processar_texto_natural(update, context, texto)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# =============== FUNÇÃO PRINCIPAL ===============

# =============== FUNÇÃO PRINCIPAL ===============

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Defina TELEGRAM_BOT_TOKEN no seu .env ou nas variáveis de ambiente."
        )

    application = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    # Comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("compras", compras_command))

    # Texto "solto" (sem /comando)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message))

    # Mensagens de voz/áudio
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, voice_message)
    )

    logger.info("Bot iniciado. Aguardando mensagens...")
    application.run_polling()   # <- sem await, sem asyncio.run aqui


if __name__ == "__main__":
    main()
