import os
import re
import datetime
import urllib.request
import yt_dlp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

# Configura o logging para vermos erros no console do Render
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- LÓGICA DE EXTRAÇÃO (adaptada para o bot) ---

def analisar_com_yt_dlp(pin_url):
    """Motor principal: Usa yt-dlp para uma análise profunda do link."""
    ydl_opts = {'quiet': True, 'noplaylist': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(pin_url, download=False)
            return info_dict, "success"
    except yt_dlp.utils.DownloadError as e:
        if "No video formats found" in str(e):
            try:
                return e.exc_info[1].info_dict, "is_image"
            except (AttributeError, IndexError, KeyError):
                return None, "yt_dlp_error"
        return None, "yt_dlp_error"
    except Exception:
        return None, "generic_error"

def extrair_imagem_direto(pin_url):
    """Motor secundário: Analisa o HTML para encontrar a imagem original."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(pin_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        match = re.search(r'"orig":\{"url":"(https://i\.pinimg\.com/originals/[^"]+)"\}', html)
        if match: return match.group(1)
        match_fallback = re.search(r'"url":"(https://i\.pinimg\.com/736x/[^"]+)"', html)
        if match_fallback: return match_fallback.group(1)
        return None
    except Exception:
        return None

def baixar_e_enviar_midia(context: CallbackContext, chat_id, url, tipo='image'):
    """Baixa a mídia para um arquivo temporário, envia para o usuário e depois apaga."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = next((e for e in ['jpg', 'png', 'webp', 'mp4'] if f'.{e}' in url.lower()), "tmp")
    nome_arquivo = f"temp_{timestamp}.{ext}"
    
    try:
        # Baixa o arquivo
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.pinterest.com/'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response, open(nome_arquivo, 'wb') as f:
            f.write(response.read())

        # Envia o arquivo para o Telegram
        context.bot.send_chat_action(chat_id, 'upload_photo' if tipo == 'image' else 'upload_video')
        with open(nome_arquivo, 'rb') as midia:
            if tipo == 'image':
                context.bot.send_photo(chat_id, photo=midia, caption=f"🔗 Link original: {url}")
            else:
                context.bot.send_video(chat_id, video=midia, caption=f"🔗 Link original: {url}")

    except Exception as e:
        context.bot.send_message(chat_id, text=f"❌ Ocorreu um erro ao baixar ou enviar a mídia.\nErro: {e}")
    finally:
        # Limpeza: apaga o arquivo do servidor, aconteça o que acontecer
        if os.path.exists(nome_arquivo):
            os.remove(nome_arquivo)

def converter_e_enviar_stream(update: Update, context: CallbackContext):
    """Converte um stream para MP4, envia e apaga."""
    query = update.callback_query
    pin_url = query.data.split('_')[1] # Pega a URL do callback data
    chat_id = query.message.chat_id
    
    query.answer()
    query.edit_message_text(text="🔄 Certo! Iniciando a conversão para .MP4... Isso pode levar um momento.")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"temp_video_{timestamp}.mp4"
    ydl_opts = {'format': 'b', 'outtmpl': nome_arquivo, 'noplaylist': True}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([pin_url])
        
        if os.path.exists(nome_arquivo):
            context.bot.send_chat_action(chat_id, 'upload_video')
            with open(nome_arquivo, 'rb') as video:
                context.bot.send_video(chat_id, video=video, caption="✅ Vídeo convertido!")
        else:
            query.message.reply_text("❌ A conversão falhou e o arquivo não foi criado.")

    except Exception as e:
        query.message.reply_text(f"❌ Ocorreu um erro na conversão.\nErro: {e}")
    finally:
        if os.path.exists(nome_arquivo):
            os.remove(nome_arquivo)

# --- HANDLERS DO BOT ---

def start(update: Update, context: CallbackContext):
    """Envia a mensagem de boas-vindas."""
    user = update.effective_user
    update.message.reply_html(
        f"Olá, {user.mention_html()}!\n\nSou seu assistente para o Pinterest. Apenas me envie um link de um Pin (vídeo, imagem ou story) e eu extrairei a mídia para você."
    )

def processar_link(update: Update, context: CallbackContext):
    """Processa o link enviado pelo usuário."""
    chat_id = update.message.chat_id
    pin_url = update.message.text.strip()
    
    msg = update.message.reply_text("🕵️  Entendido! Analisando seu link...", quote=True)

    info, status = analisar_com_yt_dlp(pin_url)

    # Caso 1: VÍDEO(S)
    if status == "success" and info.get('formats'):
        msg.edit_text("✨ Detectado: VÍDEO ✨")
        
        # Procura por MP4 direto
        for f in reversed(info['formats']):
            if f.get('ext') == 'mp4' and f.get('protocol') in {'http', 'https', 'https'}:
                mp4_url = f['url']
                context.bot.send_message(chat_id, f"✅ Encontrei um link .MP4 direto!\n\n{mp4_url}")
                # Pergunta se quer que o bot baixe e envie
                keyboard = [[InlineKeyboardButton("Sim, baixar e me enviar", callback_data=f"download_{mp4_url}")]]
                update.message.reply_text("Quer que eu baixe este vídeo para você?", reply_markup=InlineKeyboardMarkup(keyboard))
                return
        
        # Se não achou MP4, oferece conversão do stream
        for f in reversed(info['formats']):
            if 'm3u8' in f.get('protocol', ''):
                keyboard = [[InlineKeyboardButton("Sim, converter para MP4", callback_data=f"convert_{pin_url}")]]
                update.message.reply_text(
                    "⚠️ Encontrei apenas um link de stream (.m3u8).\n\nDeseja que eu o converta para um arquivo .MP4 e o envie?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

    # Caso 2: IMAGEM
    if status == "is_image" or status == "yt_dlp_error":
        msg.edit_text("✨ Detectado: IMAGEM ✨ (Usando método de extração direta)")
        img_url = extrair_imagem_direto(pin_url)
        if img_url:
            baixar_e_enviar_midia(context, chat_id, img_url, 'image')
        else:
            msg.edit_text("❌ Falha. Não consegui encontrar uma imagem de alta qualidade neste link.")
        return

    # Caso 3: Falha total
    msg.edit_text("❌ Desculpe, não consegui extrair nenhuma mídia válida deste link.")

def button_handler(update: Update, context: CallbackContext):
    """Lida com cliques nos botões inline."""
    query = update.callback_query
    action, data = query.data.split('_', 1)

    if action == 'convert':
        converter_e_enviar_stream(update, context)
    elif action == 'download':
        query.answer()
        query.edit_message_text("✅ Certo! Baixando e enviando o vídeo...")
        baixar_e_enviar_midia(context, query.message.chat_id, data, 'video')

def main():
    """Inicia o bot."""
    TOKEN = os.environ.get("TOKEN_DO_BOT")
    if not TOKEN:
        raise ValueError("Token não encontrado! Configure a variável de ambiente TOKEN_DO_BOT.")

    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

    # Comandos
    dispatcher.add_handler(CommandHandler("start", start))

    # Mensagens (para links)
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, processar_link))

    # Botões
    dispatcher.add_handler(CallbackQueryHandler(button_handler))

    # Inicia o Bot
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
