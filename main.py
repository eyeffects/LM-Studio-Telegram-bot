import logging
from telegram import ForceReply, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    CallbackQueryHandler, 
    ConversationHandler
)
import openai
from collections import defaultdict
import json
import os
import tiktoken
from datetime import datetime

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# OpenAI setup
openai.api_type = "open_ai"
openai.api_base = "http://localhost:1234/v1"
openai.api_key = "Whatever"

# User-specific chat histories
user_chats = defaultdict(lambda: {
    "active_chat": "default",
    "chats": {
        "default": [{'role': 'system', 'content': 'You are a helpful assistant. Keep replies within 20 words'}]
    }
})

# Directory to store chat logs
CHAT_LOGS_DIR = "chat_logs"

changelog = []
help_menu = "Default help menu text. Admins can update this."

# Add new conversation states
CHOOSING, RENAME_CHAT, ADMIN_MENU, ADMIN_BROADCAST, ADMIN_EDIT_CHANGELOG, ADMIN_EDIT_HELP = range(6)

# List of admin user IDs (replace with actual admin user IDs)
ADMIN_IDS = [YOUR ID]

BUFFER_MESSAGES = 5  # Number of recent messages to keep in the buffer

# Maximum token limit (adjust as needed)
MAX_TOKENS = 8000
SUMMARY_TOKENS = 1000  # Adjust this value to control summary length

# Initialize tokenizer
tokenizer = tiktoken.get_encoding("cl100k_base")

def count_tokens(messages):
    return sum(len(tokenizer.encode(msg['content'])) for msg in messages)

def summarize_history(messages):
    conversation = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages[1:-BUFFER_MESSAGES]])
    
    summary_prompt = f"Summarize the following conversation concisely, retaining key points and context:\n\n{conversation}\n\nSummary:"
    
    try:
        response = openai.ChatCompletion.create(
            model='gpt-4',
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.5,
            max_tokens=SUMMARY_TOKENS
        )
        summary = response.choices[0].message.content.strip()
        logger.info("Chat history summarized successfully")
    except Exception as e:
        logger.error(f"Error in summarization: {e}")
        summary = "Error in summarization. Truncating history instead."
    
    return [
        messages[0],  # Keep the original system message
        {"role": "assistant", "content": f"Previous conversation summary: {summary}"}
    ] + messages[-BUFFER_MESSAGES:]  # Append the most recent messages

def manage_history(messages, max_tokens):
    current_tokens = count_tokens(messages)
    if current_tokens > max_tokens:
        logger.info(f"Token count ({current_tokens}) exceeded limit ({max_tokens}). Summarizing history.")
        summarized = summarize_history(messages)
        while count_tokens(summarized) > max_tokens and len(summarized) > BUFFER_MESSAGES + 2:
            # If the summarized version is still too long, remove the oldest non-essential message
            summarized.pop(2)  # Remove the message after the system message and summary
        return summarized
    return messages

def load_chat_history():
    global user_chats
    if not os.path.exists(CHAT_LOGS_DIR):
        os.makedirs(CHAT_LOGS_DIR)
    for filename in os.listdir(CHAT_LOGS_DIR):
        if filename.endswith(".json"):
            user_id = int(filename.split(".")[0])
            with open(os.path.join(CHAT_LOGS_DIR, filename), "r") as f:
                user_chats[user_id] = json.load(f)
            for chat_name in user_chats[user_id]["chats"]:
                user_chats[user_id]["chats"][chat_name] = manage_history(user_chats[user_id]["chats"][chat_name], MAX_TOKENS)

def save_chat_history(user_id):
    filename = f"{user_id}.json"
    with open(os.path.join(CHAT_LOGS_DIR, filename), "w") as f:
        json.dump(user_chats[user_id], f)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!. My Name is Bot. I can have multiple conversations with you. Use /chats to manage your chats!",
        reply_markup=ForceReply(selective=True),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "I can help you with anything! Here are the available commands:\n"
        "/chats - Manage your chats\n"
        "/clear - Clear your current chat history\n"
        "/summarize - Manually trigger a summary of your current chat"
    )

async def chats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    active_chat = user_chats[user_id]["active_chat"]
    
    keyboard = [
        [InlineKeyboardButton("➕ New Chat", callback_data="new_chat")],
    ]
    
    for chat_name in user_chats[user_id]["chats"]:
        button_text = f"✅ {chat_name}" if chat_name == active_chat else chat_name
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_chat:{chat_name}")])
    
    if active_chat != "default":
        keyboard.extend([
            [InlineKeyboardButton("✏️ Rename Current Chat", callback_data="rename_chat")],
            [InlineKeyboardButton("🗑️ Delete Current Chat", callback_data="delete_chat")]
        ])
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔧 Admin Menu", callback_data="admin_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Current chat: {active_chat}\n\nSelect a chat, create a new one, or manage existing chats:", reply_markup=reply_markup)
    return CHOOSING


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "new_chat":
        chat_number = len(user_chats[user_id]["chats"]) + 1
        new_chat_name = f"Chat {chat_number}"
        user_chats[user_id]["chats"][new_chat_name] = [{'role': 'system', 'content': 'You are a helpful assistant. Keep replies within 20 words'}]
        user_chats[user_id]["active_chat"] = new_chat_name
        await query.edit_message_text(f"Created and switched to {new_chat_name}")
        return ConversationHandler.END
    elif data.startswith("select_chat:"):
        chat_name = data.split(":")[1]
        user_chats[user_id]["active_chat"] = chat_name
        await query.edit_message_text(f"Switched to {chat_name}")
        return ConversationHandler.END
    elif data == "rename_chat":
        if user_chats[user_id]["active_chat"] == "default":
            await query.edit_message_text("You cannot rename the default chat.")
            return ConversationHandler.END
        await query.edit_message_text("Please enter the new name for your current chat:")
        return RENAME_CHAT
    elif data == "delete_chat":
        if user_chats[user_id]["active_chat"] == "default":
            await query.edit_message_text("You cannot delete the default chat.")
            return ConversationHandler.END
        chat_to_delete = user_chats[user_id]["active_chat"]
        await delete_chat(update, context, user_id, chat_to_delete)
        return ConversationHandler.END
    elif data == "admin_menu" and user_id in ADMIN_IDS:
        return await admin_menu(update, context)
    elif data == "admin_broadcast" and user_id in ADMIN_IDS:
        await query.edit_message_text("Please enter the message you want to send to all users:")
        return ADMIN_BROADCAST
    elif data == "admin_edit_changelog" and user_id in ADMIN_IDS:
        await query.edit_message_text("Please enter the new changelog entry:")
        return ADMIN_EDIT_CHANGELOG
    elif data == "admin_edit_help" and user_id in ADMIN_IDS:
        await query.edit_message_text("Please enter the new help menu text:")
        return ADMIN_EDIT_HELP
    elif data == "admin_stats" and user_id in ADMIN_IDS:
        return await admin_stats(update, context)
    elif data == "admin_back" and user_id in ADMIN_IDS:
        return await chats_command(update, context)
    
    save_chat_history(user_id)
    return CHOOSING

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("Send Message to All Users", callback_data="admin_broadcast")],
        [InlineKeyboardButton("Edit Changelog", callback_data="admin_edit_changelog")],
        [InlineKeyboardButton("Edit Help Menu", callback_data="admin_edit_help")],
        [InlineKeyboardButton("View Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("Back to Chat Menu", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Admin Menu:", reply_markup=reply_markup)
    return ADMIN_MENU

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.edit_message_text("Please enter the message you want to send to all users:")
    return ADMIN_BROADCAST

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message.text
    sent_count = 0
    for user_id in user_chats.keys():
        try:
            await context.bot.send_message(chat_id=user_id, text=f"Broadcast message from admin:\n\n{message}")
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send broadcast to user {user_id}: {e}")
    
    await update.message.reply_text(f"Broadcast sent to {sent_count} users.")
    return ConversationHandler.END

async def admin_edit_changelog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.edit_message_text("Please enter the new changelog entry:")
    return ADMIN_EDIT_CHANGELOG

async def update_changelog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global changelog
    new_entry = update.message.text
    changelog.append({"date": datetime.now().strftime("%Y-%m-%d"), "entry": new_entry})
    await update.message.reply_text("Changelog updated successfully.")
    return ConversationHandler.END

async def admin_edit_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.edit_message_text("Please enter the new help menu text:")
    return ADMIN_EDIT_HELP

async def update_help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global help_menu
    help_menu = update.message.text
    await update.message.reply_text("Help menu updated successfully.")
    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    total_users = len(user_chats)
    total_chats = sum(len(user_data["chats"]) for user_data in user_chats.values())
    avg_chats_per_user = total_chats / total_users if total_users > 0 else 0
    
    stats_message = f"""
    Bot Statistics:
    - Total Users: {total_users}
    - Total Chats: {total_chats}
    - Average Chats per User: {avg_chats_per_user:.2f}
    """
    
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data="admin_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(stats_message, reply_markup=reply_markup)
    return ADMIN_MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_menu)

async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not changelog:
        await update.message.reply_text("No changelog entries yet.")
    else:
        changelog_text = "Changelog:\n\n" + "\n".join([f"{entry['date']}: {entry['entry']}" for entry in changelog[::-1]])
        await update.message.reply_text(changelog_text)


async def rename_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    new_name = update.message.text
    old_name = user_chats[user_id]["active_chat"]
    
    if old_name == "default":
        await update.message.reply_text("You cannot rename the default chat.")
        return ConversationHandler.END
    
    if new_name in user_chats[user_id]["chats"]:
        await update.message.reply_text(f"A chat with the name '{new_name}' already exists. Please choose a different name.")
        return RENAME_CHAT
    
    user_chats[user_id]["chats"][new_name] = user_chats[user_id]["chats"].pop(old_name)
    user_chats[user_id]["active_chat"] = new_name
    save_chat_history(user_id)
    
    await update.message.reply_text(f"Chat renamed from '{old_name}' to '{new_name}'.")
    return ConversationHandler.END

async def delete_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_to_delete: str) -> None:
    if chat_to_delete == "default":
        await update.callback_query.edit_message_text("You cannot delete the default chat.")
        return
    
    del user_chats[user_id]["chats"][chat_to_delete]
    user_chats[user_id]["active_chat"] = "default"
    save_chat_history(user_id)
    
    await update.callback_query.edit_message_text(f"Chat '{chat_to_delete}' has been deleted. Switched to default chat.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    active_chat = user_chats[user_id]["active_chat"]
    user_chats[user_id]["chats"][active_chat] = [{'role': 'system', 'content': 'You are a helpful assistant. Keep replies within 20 words'}]
    save_chat_history(user_id)
    await update.message.reply_text(f"Your current chat ({active_chat}) history has been cleared.")

async def manual_summarize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    active_chat = user_chats[user_id]["active_chat"]
    if len(user_chats[user_id]["chats"][active_chat]) > 1:
        user_chats[user_id]["chats"][active_chat] = summarize_history(user_chats[user_id]["chats"][active_chat])
        save_chat_history(user_id)
        await update.message.reply_text(f"Your current chat ({active_chat}) history has been summarized.")
    else:
        await update.message.reply_text("Not enough chat history to summarize.")

async def bot_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id
    active_chat = user_chats[user_id]["active_chat"]
    
    logger.info(f"Question from User {user_id} in {active_chat}: {update.message.text}")
    
    if update.message.text != '':
        user_input = update.message.text
        
        user_chats[user_id]["chats"][active_chat].append({'role': 'user', 'content': user_input})
        
        # Manage history before API call
        original_length = len(user_chats[user_id]["chats"][active_chat])
        user_chats[user_id]["chats"][active_chat] = manage_history(user_chats[user_id]["chats"][active_chat], MAX_TOKENS)
        if len(user_chats[user_id]["chats"][active_chat]) < original_length:
            await update.message.reply_text("Chat history was summarized due to length. You can use /summarize to see the current summary.")
        
        response = openai.ChatCompletion.create(
            model='gpt-4',
            messages=user_chats[user_id]["chats"][active_chat],
            temperature=0,
            max_tokens=-1
        )
        
        llm_reply = response.choices[0].message.content
        user_chats[user_id]["chats"][active_chat].append({'role': 'assistant', 'content': llm_reply})
        
        # Manage history after adding the new message
        original_length = len(user_chats[user_id]["chats"][active_chat])
        user_chats[user_id]["chats"][active_chat] = manage_history(user_chats[user_id]["chats"][active_chat], MAX_TOKENS)
        if len(user_chats[user_id]["chats"][active_chat]) < original_length:
            await update.message.reply_text("Chat history was summarized due to length. You can use /summarize to see the current summary.")
        
        # Save the updated chat history
        save_chat_history(user_id)
        
    else:
        return 

    await update.message.reply_text(llm_reply)

def main() -> None:
    # Load existing chat histories
    load_chat_history()

    application = Application.builder().token("YOUR TELEGRAM TOKEN").build()

    # Create conversation handler for chat management
    chat_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("chats", chats_command)],
        states={
            CHOOSING: [CallbackQueryHandler(button_callback)],
            RENAME_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_chat)],
            ADMIN_MENU: [CallbackQueryHandler(button_callback)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)],
            ADMIN_EDIT_CHANGELOG: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_changelog)],
            ADMIN_EDIT_HELP: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_help_menu)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("changelog", changelog_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("summarize", manual_summarize))
    application.add_handler(chat_conv_handler)
    application.add_handler(CommandHandler("chats", chats_command))
    application.add_handler(chat_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_reply))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()