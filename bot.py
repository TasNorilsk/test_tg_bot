import logging
import os
from datetime import datetime
from git import Repo
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    CallbackContext
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd

# Загрузка настроек
load_dotenv()

# Конфигурация базы данных
Base = declarative_base()
engine = create_engine('sqlite:///requests.db')
Session = sessionmaker(bind=engine)


class ServiceRequest(Base):
    __tablename__ = 'requests'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    username = Column(String(100))
    contact = Column(String(100))
    problem = Column(String(500))
    preferred_time = Column(String(100))
    timestamp = Column(DateTime)


Base.metadata.create_all(engine)

# Состояния диалога
NAME, CONTACT, PROBLEM, TIME = range(4)

# Настройка логгера
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


def sync_with_github():
    """Синхронизация с GitHub"""
    try:
        repo = Repo(os.getenv('GITHUB_REPO_URL'))
        repo.git.add('requests.db')
        repo.index.commit('Auto-commit: Обновление базы данных')
        origin = repo.remote(name='origin')
        origin.push()
    except Exception as e:
        logging.error(f"Git sync error: {e}")


def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    admins = list(map(int, os.getenv('ADMIN_IDS').split(',')))
    return user_id in admins


async def start(update: Update, context: CallbackContext):
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("📝 Создать заявку", callback_data='create_request')]
    ]

    if is_admin(update.effective_user.id):
        keyboard.append([
            InlineKeyboardButton("📊 Экспорт данных", callback_data='export_data'),
            InlineKeyboardButton("📋 Все заявки", callback_data='show_requests')
        ])

    await update.message.reply_text(
        "🔧 Сервисный бот для ремонта техники\n"
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_message(update: Update, context: CallbackContext):
    """Обработчик ключевых слов"""
    text = update.message.text.lower()
    keywords = [
        'ремонт стиральной машины',
        'не работает посудомоечная',
        'сломался холодильник',
        'ремонт кондиционера'
    ]

    if any(keyword in text for keyword in keywords):
        await update.message.reply_text("🚨 Обнаружена проблема! Пожалуйста, заполните заявку:")
        await update.message.reply_text("Введите ваше имя:")
        return NAME
    return ConversationHandler.END


async def create_request(update: Update, context: CallbackContext):
    """Начало создания заявки"""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваше имя:")
    return NAME


async def get_name(update: Update, context: CallbackContext):
    """Обработка имени"""
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📞 Введите ваш телефон или email:")
    return CONTACT


async def get_contact(update: Update, context: CallbackContext):
    """Обработка контакта"""
    if '@' not in update.message.text and not update.message.text.startswith('+'):
        await update.message.reply_text("❌ Некорректный контакт. Попробуйте снова:")
        return CONTACT

    context.user_data['contact'] = update.message.text
    await update.message.reply_text("🔧 Опишите проблему:")
    return PROBLEM


async def get_problem(update: Update, context: CallbackContext):
    """Обработка описания проблемы"""
    context.user_data['problem'] = update.message.text
    await update.message.reply_text("⏰ Укажите удобное время для связи:")
    return TIME


async def get_time(update: Update, context: CallbackContext):
    """Финальное сохранение заявки"""
    context.user_data['time'] = update.message.text

    session = Session()
    try:
        new_request = ServiceRequest(
            user_id=update.effective_user.id,
            username=context.user_data['name'],
            contact=context.user_data['contact'],
            problem=context.user_data['problem'],
            preferred_time=context.user_data['time'],
            timestamp=datetime.now()
        )
        session.add(new_request)
        session.commit()
        await update.message.reply_text("✅ Заявка успешно создана!")
    except Exception as e:
        logging.error(f"Database error: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении заявки")
    finally:
        session.close()
        sync_with_github()

    return ConversationHandler.END


async def export_data(update: Update, context: CallbackContext):
    """Экспорт данных"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("🚫 Доступ запрещен")
        return

    keyboard = [
        [
            InlineKeyboardButton("Excel", callback_data='export_excel'),
            InlineKeyboardButton("TXT", callback_data='export_txt')
        ]
    ]
    await update.callback_query.message.reply_text(
        "Выберите формат экспорта:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_export(update: Update, context: CallbackContext):
    """Обработка экспорта"""
    global file_path
    query = update.callback_query
    await query.answer()

    session = Session()
    try:
        requests = session.query(ServiceRequest).all()
        df = pd.DataFrame([(
            r.username,
            r.contact,
            r.problem,
            r.preferred_time,
            r.timestamp.strftime('%Y-%m-%d %H:%M')
        ) for r in requests], columns=['Имя', 'Контакт', 'Проблема', 'Время', 'Дата'])

        filename = f"requests_{datetime.now().strftime('%Y%m%d')}"

        if query.data == 'export_excel':
            file_path = f"{filename}.xlsx"
            df.to_excel(file_path, index=False)
        elif query.data == 'export_txt':
            file_path = f"{filename}.txt"
            df.to_csv(file_path, sep='\t', index=False)

        with open(file_path, 'rb') as f:
            await query.message.reply_document(
                document=f,
                filename=file_path
            )
        os.remove(file_path)

    except Exception as e:
        logging.error(f"Export error: {e}")
        await query.message.reply_text("❌ Ошибка экспорта")
    finally:
        session.close()


def main():
    """Запуск бота"""
    application = Application.builder().token(os.getenv('TELEGRAM_TOKEN')).build()

    # Обработчики диалога
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CallbackQueryHandler(create_request, pattern='^create_request$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contact)],
            PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_problem)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)]
        },
        fallbacks=[],
        per_message=True
    )

    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(export_data, pattern='^export_data$'))
    application.add_handler(CallbackQueryHandler(handle_export, pattern='^export_'))

    application.add_handler(conv_handler)
    application.run_polling()


if __name__ == '__main__':
    main()