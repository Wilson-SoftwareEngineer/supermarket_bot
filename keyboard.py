# keyboard.py

from telegram import KeyboardButton, ReplyKeyboardMarkup

# --- Definição dos Textos dos Botões ---
BOTAO_LISTAR = "📝 Ver Lista"
BOTAO_ADICIONAR = "➕ Adicionar"
BOTAO_REMOVER = "➖ Remover"
BOTAO_LIMPAR_LISTA = "🗑️ Limpar Lista"  # Sugestão de melhoria
BOTAO_MODO_COMPRAS = "🛒 Iniciar Compras"
BOTAO_SAIR_COMPRAS = "🏠 Voltar ao Menu"
BOTAO_AJUDA = "❓ Ajuda"
BOTAO_CANCELAR = "❌ Cancelar Ação" # Novo botão para cancelar digitação

# --- Layout do Teclado Principal ---
REPLY_KEYBOARD_NORMAL = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BOTAO_ADICIONAR), KeyboardButton(BOTAO_REMOVER)],
        [KeyboardButton(BOTAO_LISTAR), KeyboardButton(BOTAO_MODO_COMPRAS)],
        [KeyboardButton(BOTAO_AJUDA)]
    ],
    resize_keyboard=True
)

# --- Layout do Teclado Modo Compras ---
REPLY_KEYBOARD_COMPRAS = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BOTAO_LISTAR)],
        [KeyboardButton(BOTAO_SAIR_COMPRAS), KeyboardButton(BOTAO_AJUDA)],
    ],
    resize_keyboard=True
)

# --- Layout do Teclado de Cancelamento ---
# Usado quando o bot está esperando o usuário digitar um nome
REPLY_KEYBOARD_CANCELAR = ReplyKeyboardMarkup(
    [[KeyboardButton(BOTAO_CANCELAR)]],
    resize_keyboard=True,
    one_time_keyboard=True
)