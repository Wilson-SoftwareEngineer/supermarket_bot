# main.py
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

# Importação atualizada do keyboard.py
from keyboard import (
    REPLY_KEYBOARD_NORMAL,
    REPLY_KEYBOARD_COMPRAS,
    REPLY_KEYBOARD_CANCELAR,
    BOTAO_LISTAR,
    BOTAO_ADICIONAR,
    BOTAO_REMOVER,
    BOTAO_MODO_COMPRAS,
    BOTAO_SAIR_COMPRAS,
    BOTAO_AJUDA,
    BOTAO_CANCELAR
)

# =============== CONFIGURAÇÃO BÁSICA ===============
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# =============== MODELO DE DADOS ===============

@dataclass
class ItemLista:
    nome: str
    em_carrinho: bool = False
    preco: Optional[float] = None


@dataclass
class EstadoUsuario:
    itens: Dict[str, ItemLista] = field(default_factory=dict)
    modo_compras: bool = False
    # NOVO CAMPO: Armazena o que o bot está esperando o usuário digitar ('adicionar' ou 'remover')
    acao_pendente: Optional[str] = None 


# =============== FUNÇÃO DE TRANSCRIÇÃO (STT) ===============
# (Mantida idêntica à sua original)
def transcribe_voice(file_path: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não definida.")

    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Biblioteca 'openai' não instalada.") from e

    client = OpenAI(api_key=api_key)

    with open(file_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            model="whisper-1", file=f, language="pt"
        )
    return transcription.text


# =============== FUNÇÕES DE LÓGICA (CORE) ===============

def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> EstadoUsuario:
    if "estado" not in context.user_data:
        context.user_data["estado"] = EstadoUsuario()
    return context.user_data["estado"]

def adicionar_item(estado: EstadoUsuario, nome_item: str) -> str:
    nome = nome_item.strip().lower()
    if not nome:
        return "Nome inválido."
    if nome in estado.itens:
        return f"❌ '{nome_item}' já está na lista."
    estado.itens[nome] = ItemLista(nome=nome)
    return f"✅ '{nome_item}' adicionado!"

def remover_item(estado: EstadoUsuario, nome_item: str) -> str:
    nome = nome_item.strip().lower()
    if not nome:
        return "Nome inválido."
    if nome not in estado.itens:
        return f"⚠️ '{nome_item}' não encontrado na lista."
    del estado.itens[nome]
    return f"🗑️ '{nome_item}' removido."

def listar_itens(estado: EstadoUsuario) -> str:
    if not estado.itens:
        return "Sua lista está vazia. 🦗"

    pendentes = []
    comprados = []
    total = 0.0

    for item in estado.itens.values():
        if item.em_carrinho:
            preco_txt = f"R$ {item.preco:.2f}" if item.preco is not None else "-"
            comprados.append(f"✅ {item.nome} ({preco_txt})")
            if item.preco is not None:
                total += item.preco
        else:
            pendentes.append(f"⬜ {item.nome}")

    msg = ["📝 *LISTA DE COMPRAS*\n"]
    
    if pendentes:
        msg.append("*Falta pegar:*")
        msg.extend(pendentes)
    else:
        msg.append("🎉 Nada pendente!")
    
    msg.append("")
    
    if comprados:
        msg.append("*Já no carrinho:*")
        msg.extend(comprados)
        msg.append(f"\n💰 *Total:* R$ {total:.2f}")
    
    return "\n".join(msg)

def marcar_item_comprado(estado: EstadoUsuario, texto: str) -> str:
    # 1. Encontrar TODOS os padrões numéricos na string
    # O regex busca números inteiros ou com ponto/vírgula
    matches = re.findall(r"(\d+[.,]\d+|\d+)", texto)

    if not matches:
        return "⚠️ Não encontrei o preço. Tente: 'peguei leite 4.50'"

    # 2. Assumimos que o preço é o ÚLTIMO número mencionado
    preco_str_bruta = matches[-1]
    
    # Converter para float (troca vírgula por ponto)
    try:
        preco = float(preco_str_bruta.replace(",", "."))
    except ValueError:
        return "⚠️ Erro ao entender o valor numérico."

    # 3. Separar o nome do item
    # Usamos 'rpartition' para dividir a string na ÚLTIMA ocorrência desse preço
    # Ex: "Peguei leite por 4.50 reais" -> ("Peguei leite por ", "4.50", " reais")
    parte_antes, _, parte_depois = texto.rpartition(preco_str_bruta)

    # Limpeza do nome do produto (parte_antes)
    texto_limpo = parte_antes.lower()
    
    # Remover palavras comuns de início de frase e preposições finais
    palavras_inicio = [
        "peguei o", "peguei a", "peguei", 
        "comprei o", "comprei a", "comprei",
        "marquei o", "marquei a", "marquei",
        "coloquei", "adicionar", "custou"
    ]
    
    for prefixo in palavras_inicio:
        if texto_limpo.strip().startswith(prefixo):
            # remove o prefixo
            texto_limpo = texto_limpo.strip()[len(prefixo):]
    
    # Remover preposições soltas no final do nome ("leite por", "leite custou")
    palavras_fim = [" por", " custou", " valor", " no valor de"]
    for sufixo in palavras_fim:
        if texto_limpo.endswith(sufixo):
            texto_limpo = texto_limpo[:-len(sufixo)]

    nome_item = texto_limpo.strip()
    
    if not nome_item:
        return f"⚠️ Entendi o preço (R$ {preco:.2f}), mas não o nome do produto."

    # --- Lógica de Atualização do Estado ---
    nome_chave = nome_item.lower()
    
    msg_base = ""
    if nome_chave not in estado.itens:
        estado.itens[nome_chave] = ItemLista(nome=nome_item, em_carrinho=True, preco=preco)
        msg_base = f"➕ '{nome_item}' adicionado e marcado"
    else:
        item = estado.itens[nome_chave]
        item.em_carrinho = True
        item.preco = preco
        msg_base = f"✅ '{nome_item}' marcado no carrinho"

    # Calcular total parcial
    total = sum(i.preco for i in estado.itens.values() if i.em_carrinho and i.preco is not None)

    return f"{msg_base} (R$ {preco:.2f}).\n💰 Total parcial: R$ {total:.2f}"

# =============== LÓGICA PRINCIPAL (TEXTO & BOTÕES) ===============

async def processar_texto_natural(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    if not texto:
        return

    estado = get_user_state(context)
    texto_original = texto.strip()
    texto_lower = texto_original.lower()

    # --- 1. VERIFICAR BOTÕES DE COMANDO IMEDIATO ---
    
    # Se o usuário clicar em "Cancelar", limpamos o estado de espera
    if texto_original == BOTAO_CANCELAR:
        estado.acao_pendente = None
        await update.message.reply_text("Ação cancelada.", reply_markup=REPLY_KEYBOARD_NORMAL)
        return

    if texto_original == BOTAO_LISTAR:
        estado.acao_pendente = None # Reseta qualquer espera
        keyboard = REPLY_KEYBOARD_COMPRAS if estado.modo_compras else REPLY_KEYBOARD_NORMAL
        await update.message.reply_text(listar_itens(estado), parse_mode="Markdown", reply_markup=keyboard)
        return

    if texto_original == BOTAO_AJUDA:
        estado.acao_pendente = None
        await help_command(update, context)
        return

    if texto_original == BOTAO_MODO_COMPRAS:
        return await compras_command(update, context)

    if texto_original == BOTAO_SAIR_COMPRAS:
        estado.modo_compras = False
        estado.acao_pendente = None
        await update.message.reply_text(
            "🏠 Você saiu do modo compras.",
            reply_markup=REPLY_KEYBOARD_NORMAL
        )
        return

    # --- 2. VERIFICAR SE O USUÁRIO CLICOU NOS BOTÕES DE AÇÃO (ADD/REMOVE) ---
    
    if texto_original == BOTAO_ADICIONAR:
        estado.acao_pendente = "adicionar"
        await update.message.reply_text(
            "✍️ *Digite o nome do item* para adicionar (ou fale por áudio):",
            parse_mode="Markdown",
            reply_markup=REPLY_KEYBOARD_CANCELAR # Mostra botão cancelar
        )
        return

    if texto_original == BOTAO_REMOVER:
        estado.acao_pendente = "remover"
        await update.message.reply_text(
            "🗑️ *Digite o nome do item* para remover:",
            parse_mode="Markdown",
            reply_markup=REPLY_KEYBOARD_CANCELAR
        )
        return

    # --- 3. VERIFICAR SE O BOT ESTÁ ESPERANDO UMA RESPOSTA (ACAO_PENDENTE) ---
    
    if estado.acao_pendente:
        # Se chegamos aqui, o texto recebido é o NOME DO ITEM
        nome_item = texto_original
        
        # Proteção: Se o usuário clicou num botão de outro menu sem querer, 
        # evitamos adicionar o nome do botão como item.
        if nome_item in [BOTAO_LISTAR, BOTAO_MODO_COMPRAS, BOTAO_AJUDA]:
            estado.acao_pendente = None
            # Reprocessa como comando novo recursivamente
            await processar_texto_natural(update, context, nome_item)
            return

        if estado.acao_pendente == "adicionar":
            msg = adicionar_item(estado, nome_item)
        elif estado.acao_pendente == "remover":
            msg = remover_item(estado, nome_item)
        
        # Limpa o estado e retorna ao teclado normal
        estado.acao_pendente = None
        await update.message.reply_text(msg, reply_markup=REPLY_KEYBOARD_NORMAL)
        return

    # --- 4. COMANDOS EM LINGUAGEM NATURAL (COMANDO DE VOZ DIRETO OU TEXTO SOLTO) ---

    # Se não clicou em botão e não estava esperando input, tenta entender a frase
    
    if "fazendo compras" in texto_lower:
        return await compras_command(update, context)

    if estado.modo_compras:
        if any(texto_lower.startswith(pref) for pref in ["peguei", "marcar", "marquei"]):
            msg = marcar_item_comprado(estado, texto_original)
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=REPLY_KEYBOARD_COMPRAS)
            return

    # Comandos diretos: "adicionar leite", "remover arroz"
    if texto_lower.startswith("adicionar ") or texto_lower.startswith("adiciona "):
        nome = texto_original.split(maxsplit=1)[1]
        msg = adicionar_item(estado, nome)
        await update.message.reply_text(msg, reply_markup=REPLY_KEYBOARD_NORMAL)
        return

    if texto_lower.startswith("remover ") or texto_lower.startswith("tira "):
        nome = texto_original.split(maxsplit=1)[1]
        msg = remover_item(estado, nome)
        await update.message.reply_text(msg, reply_markup=REPLY_KEYBOARD_NORMAL)
        return

    if "listar" in texto_lower or "lista" in texto_lower:
        await update.message.reply_text(listar_itens(estado), parse_mode="Markdown", reply_markup=REPLY_KEYBOARD_NORMAL)
        return

    # Se chegou aqui, não entendeu nada
    keyboard = REPLY_KEYBOARD_COMPRAS if estado.modo_compras else REPLY_KEYBOARD_NORMAL
    await update.message.reply_text(
        "🤔 Não entendi.\nUse os botões ou diga 'adicionar [item]'.",
        reply_markup=keyboard
    )


# =============== HANDLERS PADRÃO ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    get_user_state(context)
    await update.message.reply_text(
        "👋 *Olá! Eu sou seu assistente de compras.*\n\n"
        "Toque em *Adicionar* e digite o nome do produto, ou use o microfone!",
        parse_mode="Markdown",
        reply_markup=REPLY_KEYBOARD_NORMAL,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = REPLY_KEYBOARD_COMPRAS if get_user_state(context).modo_compras else REPLY_KEYBOARD_NORMAL
    await update.message.reply_text(
        "💡 *Como usar:*\n\n"
        "1. Clique em '➕ Adicionar' e digite o nome.\n"
        "2. Ou fale: _'Adicionar café'_\n"
        "3. No mercado, clique em '🛒 Iniciar Compras'.\n"
        "4. Vá falando: _'Peguei leite por 5 reais'_",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def compras_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = get_user_state(context)
    estado.modo_compras = True
    estado.acao_pendente = None # Garante que não está esperando input
    await update.message.reply_text(
        "🛒 *Modo Compras Ativado*\n\n"
        "Vá enviando áudios ou textos conforme pega os produtos:\n"
        "Ex: _'Peguei sabão 15,90'_",
        parse_mode="Markdown",
        reply_markup=REPLY_KEYBOARD_COMPRAS,
    )

async def voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handler de voz padronizado
    if not (update.message.voice or update.message.audio):
        return

    file = await (update.message.voice or update.message.audio).get_file()
    
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await file.download_to_drive(tmp_path)
        texto = transcribe_voice(tmp_path)
        await processar_texto_natural(update, context, texto)
    except Exception as e:
        logger.error(f"Erro voz: {e}")
        await update.message.reply_text("❌ Erro ao processar áudio.")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await processar_texto_natural(update, context, update.message.text)


# =============== MAIN ===============

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN no .env")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("compras", compras_command))
    
    # Handlers genéricos (Texto e Voz)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_message))

    logger.info("Bot rodando! 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()