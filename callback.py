import telebot
import os
import random
from randoms import *
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from openai import OpenAI

# Импорты сервисов
from database_service import DatabaseService
from ai_service import AIService
from parser_service import ParserService
from dotenv import load_dotenv

load_dotenv()
bot = telebot.TeleBot(os.getenv("TOKEN"))

# Инициализация сервисов
db_service = DatabaseService()
ai_service = AIService()
parser_service = ParserService()


# Глобальные переменные
user_results = {}
temp_place_data = {}  # Для хранения данных о новых местах в процессе добавления
CHANNEL_ID = -1002591278253
client = OpenAI(
    api_key=os.getenv("KEY"),
    base_url=os.getenv("URL"),
    timeout=20.0,
    max_retries=3
)


# РАБОТА С КАНАЛОМ
@bot.channel_post_handler(content_types=['text', 'photo'])
def handle_channel_post(message):
    if message.chat.id != CHANNEL_ID:
        return

    if message.photo:
        photo_id = message.photo[-1].file_id
        text = message.caption or ""
    else:
        photo_id = None
        text = message.text or ""

    place_data = parser_service.parse_place_info(text)

    if not place_data:
        bot.send_message(message.chat.id, "Введите место в формате:\n"
                                          "Название: Пример\n"
                                          "Описание: Пример\n"
                                          "Ключ: Пример")
        return

    if db_service.place_exists(place_data["name"]):
        bot.send_message(message.chat.id,
                         "Такое место уже существует. Пожалуйста, отправьте другое название.")
        return

    db_service.add_place_to_db(place_data, photo_id)
    bot.send_message(message.chat.id, f"Место '{place_data['name']}' успешно добавлено!")
# КОНЕЦ РАБОТЫ С ТГ КАНАЛОМ



@bot.callback_query_handler(func=lambda call: call.data == "show")
def show_callback(call):
    us_id = call.from_user.id
    visited_places = db_service.get_visited_places(us_id)
    if visited_places:
        user_results[us_id] = visited_places
        send_result(call.message.chat.id, us_id, 0)
    else:
        bot.send_message(call.message.chat.id, "Похоже, вы еще не отмечали посещенные места. 🧐")


@bot.callback_query_handler(func=lambda call: call.data == "add_user_place")
def add_user_place_callback(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    temp_place_data[user_id] = {"chat_id": call.message.chat.id, "data": {}}
    msg = bot.send_message(call.message.chat.id, "Отлично! Давайте добавим новое место. 😺\n"
                                                 "Если захотите прервать добавление места, нажмите /cancel\n"
                                                 "Для начала, как называется ваше место?")
    bot.register_next_step_handler(msg, process_place_name, user_id)


def process_place_name(message, user_id):
    if message.text in ["/cancel", "/menu"]:
        del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано.")
        start_message(message)
        return

    if db_service.place_exists(message.text):
        bot.send_message(message.chat.id, "Такое место уже есть в нашей базе, попробуйте добавить другое 😊")
        msg = bot.send_message(message.chat.id, "Попробуйте ввести другое название:")
        bot.register_next_step_handler(msg, process_place_name, user_id)
        return

    temp_place_data[user_id]["data"]["name"] = message.text
    msg = bot.send_message(message.chat.id, "Как бы вы оценили это место по шкале от 1 до 10?")
    bot.register_next_step_handler(msg, process_feedback, user_id)


def process_feedback(message, user_id):
    try:
        feedback = int(message.text)
        if not (1 <= feedback <= 10):
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите целое число от 1 до 10:")
        bot.register_next_step_handler(msg, process_feedback, user_id)
        return

    data = temp_place_data[user_id]["data"]
    data["sum_feedback"] = feedback
    data["count_user"] = 1
    data["feedback"] = round(feedback, 1)

    msg = bot.send_message(message.chat.id, "Введите комментарий о месте (или /skip чтобы пропустить):")
    bot.register_next_step_handler(msg, process_place_description, user_id)


def process_place_description(message, user_id):
    if message.text == "/skip":
        temp_place_data[user_id]["data"]["comment"] = None
        temp_place_data[user_id]["data"]["sentiment"] = None
    else:
        temp_place_data[user_id]["data"]["comment"] = message.text
        temp_place_data[user_id]["data"]["sentiment"] = ai_service.analyze_comment(message.text)

    msg = bot.send_message(message.chat.id, "Введите описание места:")
    bot.register_next_step_handler(msg, process_keys, user_id)


def process_keys(message, user_id):
    temp_place_data[user_id]["data"]["description"] = message.text
    msg = bot.send_message(message.chat.id, "Введите тэг (ключ), по которому можно найти это место:")
    bot.register_next_step_handler(msg, process_place_photo, user_id)


def process_place_photo(message, user_id):
    temp_place_data[user_id]["data"]["key"] = message.text.lower()
    msg = bot.send_message(message.chat.id, "Отправьте фото места (или нажмите /skip чтобы пропустить):")
    bot.register_next_step_handler(msg, save_new_place, user_id)


def save_new_place(message, user_id):
    data = temp_place_data[user_id]["data"]
    chat_id = temp_place_data[user_id]["chat_id"]

    if message.photo:
        data["img"] = message.photo[-1].file_id
    elif message.text and message.text.lower() == "/skip":
        data["img"] = None
    else:
        msg = bot.send_message(chat_id, "Пожалуйста, отправьте фото или нажмите /skip")
        bot.register_next_step_handler(msg, save_new_place, user_id)
        return

    db_service.add_place_to_db(data, data["img"])

    responses = random.choice(RESPONSES)
    bot.send_message(chat_id, responses)

    place_row = db_service.get_place_by_name(data["name"])
    if not place_row:
        bot.send_message(chat_id, "Ошибка сохранения места.")
        return

    place_id = place_row[0]
    db_service.mark_place_visited(
        user_id=user_id,
        place_id=place_id,
        feedback=data["sum_feedback"],
        comment=data["comment"],
        sentiment=data["sentiment"]
    )

    del temp_place_data[user_id]
    bot.send_message(chat_id, "Что дальше?")
    start_message(message)


@bot.message_handler(commands=["start"])
def start_message(message):
    user_id = message.from_user.id
    username = message.from_user.username
    db_service.add_user(user_id, username)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔍 Найти новые места", callback_data="search"))
    markup.add(InlineKeyboardButton("📌 Показать места, где был", callback_data="show"))
    markup.add(InlineKeyboardButton("➕ Добавить место", callback_data="add_user_place"))

    if db_service.check_user_role(user_id, "admin") or db_service.check_user_role(user_id, "manager"):
        markup.add(InlineKeyboardButton("Добавить место (mod)", callback_data="add_place"))
        markup.add(InlineKeyboardButton("Редактировать место (mod)", callback_data="edit_place"))
    if db_service.check_user_role(user_id, "admin"):
        markup.add(InlineKeyboardButton("Удалить место (mod)", callback_data="delete_place"))
        markup.add(InlineKeyboardButton("Удалить пользователя (mod)", callback_data="delete_user"))
        markup.add(InlineKeyboardButton("Удалить комментарий (mod)", callback_data="delete_comment"))
        markup.add(InlineKeyboardButton("Изменить роль (mod)", callback_data="assign_role"))

    greeting = random.choice(GREETINGS)
    bot.send_message(message.chat.id, f"{greeting}\nЯ ваш помощник в поиске интересных мест. Что вас интересует?",
                     reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "point")
def point_callback(call):
    user_id = call.from_user.id
    results = user_results.get(user_id)
    place_name = results[user_results["index"]][0]
    place_row = db_service.get_place_by_name(place_name)
    place_id = place_row[0]

    # Проверяем, есть ли уже запись о посещении
    if db_service.user_visited_place(user_id, place_id):
        bot.send_message(call.message.chat.id, "Вы уже отмечали это место как посещенное. 😊")
    else:
        msg = bot.send_message(
            call.message.chat.id,
            "Поделитесь, пожалуйста, вашими впечатлениями об этом месте. 🌟\n"
            "Как бы вы оценили его от 0 до 10? (10 - это потрясающе!)")
        bot.register_next_step_handler(msg, point_db0, user_id, place_id, place_name)


def point_db0(message, us_id, place_id, place_name):
    results = user_results.get(us_id)
    if not results:
        bot.send_message(message.chat.id, "ОШИБКА")
    feedback = message.text
    try:
        feedback_int = int(feedback)
        if feedback_int < 0 or feedback_int > 10:
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Кажется, вы ввели что-то не то. 😅\n"
                                                "Пожалуйста, введите целое число от 0 до 10:")
        bot.register_next_step_handler(msg, point_db0, us_id, place_id, place_name)
        return
    msg = bot.send_message(message.chat.id,
                           "Спасибо за оценку! 💖\n"
                           "Не хотите ли поделиться своими впечатлениями в комментарии?\n"
                           "(или нажмите /skip если не хотите оставлять комментарий)")
    bot.register_next_step_handler(msg, point_db, us_id, place_id, feedback_int)


def point_db(message, us_id, place_id, feedback_int):
    if message.text and message.text.lower() == "/skip":
        comment = None
        sentiment = None
        bot.send_message(
            message.chat.id,
            "Хорошо, комментарий не добавлен. 😊 Больше вы не сможете добавить комментарий к этому месту 😫")
    else:
        comment = message.text
        sentiment = ai_service.analyze_comment(comment)
        bot.send_message(
            message.chat.id,
            "Спасибо за ваш отзыв! 🙏\n"
            "Ваше мнение очень важно для нас и других пользователей."
        )
    # Находим place_id по названию места
    db_service.mark_place_visited(
        user_id=us_id,
        place_id=place_id,
        feedback=feedback_int,
        comment=comment,
        sentiment=sentiment
    )
    bot.send_message(message.chat.id, "Место добавлено в посещённые 😊")


@bot.message_handler(commands=["menu"])
def start_message(message):
    markup = InlineKeyboardMarkup()
    user_id = message.from_user.id
    markup.add(InlineKeyboardButton("🔍 Найти новые места", callback_data="search"))
    markup.add(InlineKeyboardButton("📌 Показать места, где был", callback_data="show"))
    markup.add(InlineKeyboardButton("➕ Добавить место", callback_data="add_user_place"))

    if  db_service.check_user_role(user_id, "admin") or  db_service.check_user_role(user_id, "manager"):
        markup.add(InlineKeyboardButton("Добавить место(mod)", callback_data="add_place"))
        markup.add(InlineKeyboardButton("Редактировать место(mod)", callback_data="edit_place"))

    if  db_service.check_user_role(user_id, "admin"):
        markup.add(InlineKeyboardButton("Удалить место(mod)", callback_data="delete_place"))
        markup.add(InlineKeyboardButton("Удалить пользователя(mod)", callback_data="delete_user"))
        markup.add(InlineKeyboardButton("Удалить комментарий(mod)", callback_data="delete_comment"))
        markup.add(InlineKeyboardButton("Изменить роль(mod)", callback_data="assign_role"))

    bot.send_message(message.chat.id, "<b>🎀ГЛАВНОЕ МЕНЮ🎀</b>", reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "search")
def search_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
                     "Что бы вы хотели найти? 🔍\n"
                     "Можете ввести название места или ключевое слово.\n"
                     "Например: 'прогулка', 'театр', 'музей', 'ресторан'")


@bot.callback_query_handler(func=lambda call: call.data == "comments")
def comments_callback(call):
    print("YES0")
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    results = user_results.get(user_id)
    place_name = results[user_results["index"]][0]
    place = db_service.get_place_by_name(place_name)
    if not place:
        bot.send_message(call.message.chat.id, "Место не найдено.")
        return
    place_id = place[0]
    total_comments = db_service.get_total_comments(place_id)
    user_results["number"] = total_comments
    user_results["comment_index"] = 0
    if total_comments > 0:
        # Находим первый непустой комментарий
        offset = 0
        while True:
            comment_data = db_service.get_next_non_empty_comment(place_id, offset)
            comment_text, comment_user_id, username, sentiment, feedback = comment_data
            if comment_text:  # Если комментарий не пустой
                sentiment = comment_data[3]  # Извлекаем тональность из БД
                feedback = comment_data[4]
                emoji = "😊" if sentiment == "хороший" else "😞" if sentiment == "плохой" else "😐"
                comment = (f"{emoji} <b>{username}</b> оценил на {feedback}/10:\n"
                           f"{comment_text}")
                markup = InlineKeyboardMarkup()
                if total_comments > offset + 1:
                    markup.add(InlineKeyboardButton("Вперёд", callback_data=f"next2_{offset + 1}"))
                bot.send_message(call.message.chat.id, comment, reply_markup=markup, parse_mode="HTML")
                return
            else:
                offset += 1
                if offset >= total_comments:
                    break
    else:
        markup = InlineKeyboardMarkup()
        bot.send_message(call.message.chat.id, "Пока никто не оставил комментариев об этом месте. 😕",
                         reply_markup=markup)

@bot.message_handler(content_types=["text"])
def get_text_message(message):
    us_id = message.from_user.id
    username = message.from_user.username
    db_service.add_user(us_id, username)
    user_text = message.text
    db_service.save_query(us_id, user_text)
    user_results[us_id] = None
    # Проверяем, не находится ли пользователь в процессе добавления места
    if us_id in temp_place_data:
        bot.send_message(
            message.chat.id, "⚠️ Завершите действие или нажмите /cancel"
        )
        return
    results = db_service.search_places(user_text)
    if not results:
        bot.send_message(
            message.chat.id, "🔍 Ничего не найдено. Попробуйте другой запрос."
        )
        return
    user_results[us_id] = results
    user_results["index"] = 0
    send_result(message.chat.id, us_id, 0)


def send_result(chat_id, user_id, index):
    results = []
    user_results["index"] = index
    for x in range(len(user_results[user_id])):
        results.append(user_results[user_id][x])
    place = results[index]
    message_text = (
        f"📍 Место: {place[0]}\n"
        f"✏️ Рейтинг: {place[1]} ⭐\n"
        f"📝 Описание: {place[2]}\n\n"
    )

    markup = InlineKeyboardMarkup()
    # Кнопка "Скрыть"
    markup.add(InlineKeyboardButton("✖️ Скрыть", callback_data="unseen"))
    markup.add(InlineKeyboardButton("✅ Отметить посещённым", callback_data="point"))
    markup.add(InlineKeyboardButton("💬 Комментарии", callback_data="comments"))

    # Кнопки пагинации
    buttons = []
    if index > 0:
        buttons.append(
            InlineKeyboardButton("< Назад", callback_data=f"prev_{index - 1}")
        )
    if index < len(results) - 1:
        buttons.append(
            InlineKeyboardButton("Вперёд >", callback_data=f"next_{index + 1}")
        )
    if buttons:
        markup.row(*buttons)
    if place[3]:
        bot.send_photo(
            chat_id, photo=f"{place[3]}", caption=message_text, reply_markup=markup
        )
    else:
        bot.send_message(chat_id, message_text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data == "unseen":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return

    us_id = call.from_user.id
    results = user_results.get(us_id)

    if not results:
        bot.answer_callback_query(
            call.id, "🔍 Результаты устарели, выполните новый поиск"
        )
        return

    if call.data.startswith("prev_"):
        new_index = int(call.data.split("_")[1])
        if 0 <= new_index < len(results):
            edit_result(call.message, results, new_index)

    elif call.data.startswith("next_"):
        new_index = int(call.data.split("_")[1])
        if 0 <= new_index < len(results):
            edit_result(call.message, results, new_index)
    bot.answer_callback_query(call.id)


def edit_result(message, results, new_index):
    user_results["index"] = new_index
    place = results[new_index]
    message_text = (
        f"📍 Место: {place[0]}\n"
        f"✏️ Рейтинг: {place[1]} ⭐\n"
        f"📝 Описание: {place[2]}\n\n"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✖️ Скрыть", callback_data="unseen"))
    markup.add(InlineKeyboardButton("✅ Отметить посещённым", callback_data="point"))
    markup.add(InlineKeyboardButton("💬 Комментарии", callback_data="comments"))
    buttons = []

    if new_index > 0:
        buttons.append(
            InlineKeyboardButton("< Назад", callback_data=f"prev_{new_index - 1}")
        )

    if new_index < len(results) - 1:
        buttons.append(
            InlineKeyboardButton("Вперёд >", callback_data=f"next_{new_index + 1}")
        )

    if buttons:
        markup.row(*buttons)

    if place[3]:  # Если есть фото
        if message.photo:  # Если текущее сообщение содержит фото
            new_media = InputMediaPhoto(media=place[3], caption=message_text)
            bot.edit_message_media(
                media=new_media,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )
        else:  # Если текущее сообщение не содержит фото
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_photo(
                message.chat.id,
                photo=place[3],
                caption=message_text,
                reply_markup=markup,
            )
    else:  # Если нет фото
        if message.photo:  # Если текущее сообщение содержит фото
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, message_text, reply_markup=markup)
        else:  # Если текущее сообщение не содержит фото
            bot.edit_message_text(
                message_text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )


@bot.callback_query_handler(func=lambda call: call.data.startswith(("next2_", "prev2_")))
def handle_comment_pagination(call):
    user_id = call.from_user.id
    results = user_results.get(user_id)
    # Получаем ID места
    place_name = (results[user_results["index"]][0],)
    place_id = db_service.get_place_by_name(place_name)[0]
    # Определяем направление пагинации
    if call.data.startswith("next2_"):
        offset = int(call.data.split("_")[1])
    else:
        offset = int(call.data.split("_")[1])
    # Получаем следующий/предыдущий непустой комментарий
    comment_data = db_service.get_next_non_empty_comment(place_id, offset) #может ошибка
    if comment_data:
        comment_text, comment_user_id, username, sentiment, feedback = comment_data
        markup = InlineKeyboardMarkup()
        buttons = []
        # Проверяем, есть ли предыдущие комментарии
        if offset > 0:
            buttons.append(InlineKeyboardButton("Назад", callback_data=f"prev2_{offset - 1}"))
        # Проверяем, есть ли следующие комментарии
        has_next = db_service.has_next(place_id, offset)
        if has_next:
            buttons.append(InlineKeyboardButton("Вперёд", callback_data=f"next2_{offset + 1}"))
        if buttons:
            markup.row(*buttons)
        feedback = comment_data[4]
        sentiment = comment_data[3]  # Извлекаем тональность из БД
        emoji = "😊" if sentiment == "хороший" else "😞" if sentiment == "плохой" else "😐"
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{emoji} <b>{username}</b> оценил на {feedback}/10:\n"
                 f"{comment_text}",
            parse_mode="HTML",
            reply_markup=markup
        )


@bot.callback_query_handler(func=lambda call: call.data == "add_place")
def add_place_callback(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    prompt = (
        "Отправьте фотографию места (опционально), введите данные о месте в формате:\n"
        "Название: Пример\n"
        "Описание: Пример\n"
        "Ключ: Пример"
    )
    msg = bot.send_message(call.message.chat.id, prompt)
    bot.register_next_step_handler(msg, process_place_input, user_id)


def process_place_input(message):
    if message.text in ["/cancel", "/menu"]:
        start_message(message)
        return

    if message.photo:
        photo_id = message.photo[-1].file_id
        text = message.caption or ""
    else:
        photo_id = None
        text = message.text or ""

    place_data = parser_service.parse_place_info(text)
    if not place_data:
        msg = bot.send_message(message.chat.id, "Введите данные в правильном формате:")
        bot.register_next_step_handler(msg, process_place_input)
        return

    if db_service.place_exists(place_data["name"]):
        msg = bot.send_message(message.chat.id, "Такое место уже существует. Введите данные заново:")
        bot.register_next_step_handler(msg, process_place_input)
        return

    db_service.add_place_to_db(place_data, photo_id)
    bot.send_message(message.chat.id, f"Место '{place_data['name']}' успешно добавлено!")




@bot.callback_query_handler(func=lambda call: call.data == "edit_place")
def edit_place_callback(call):
    user_id = call.from_user.id
    if not db_service.check_user_role(user_id, "admin") and not db_service.check_user_role(user_id, "manager"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return

    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Введите ID места для редактирования:")
    bot.register_next_step_handler(msg, select_place_for_edit, user_id)


def select_place_for_edit(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        start_message(message)
        return
    place_id = message.text
    place = db_service.get_place_by_id(place_id)
    if not place:
        msg = bot.send_message(message.chat.id, "Место отсутствует в базе данных. Введите ID места заново:")
        bot.register_next_step_handler(msg, select_place_for_edit, user_id)
        return

    # Сохраняем данные для редактирования
    temp_place_data[user_id] = {
        "place_id": place[0],
        "original": {
            "name": place[1],
            "description": place[2],
            "img": place[3]
        },
        "updates": {}
    }
    # Начинаем редактирование названия
    msg = bot.send_message(
        message.chat.id,
        f"Текущее название: {place[1]}\nВведите новое название или /skip, чтобы оставить как есть:"
    )
    bot.register_next_step_handler(msg, edit_name_step, user_id)



def edit_name_step(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        start_message(message)
        return

    if message.text.lower() == "/skip":
        temp_place_data[user_id]["updates"]["name"] = temp_place_data[user_id]["original"]["name"]
        msg = bot.send_message(
            message.chat.id,
            f"Текущее описание: {temp_place_data[user_id]['original']['description']}\n"
            f"Введите новое описание или /skip, чтобы оставить как есть:"
        )
        bot.register_next_step_handler(msg, edit_description_step, user_id)
        return

    new_name = message.text.strip()
    if db_service.place_exists(message.text):
        msg = bot.send_message(message.chat.id, "Такое название уже занято. Попробуйте другое:")
        bot.register_next_step_handler(msg, edit_name_step, user_id)
    else:
        temp_place_data[user_id]["updates"]["name"] = new_name
        msg = bot.send_message(
            message.chat.id,
            f"Текущее описание: {temp_place_data[user_id]['original']['description']}\n"
            f"Введите новое описание или /skip, чтобы оставить как есть:"
        )
        bot.register_next_step_handler(msg, edit_description_step, user_id)



def edit_description_step(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        start_message(message)
        return

    msg = bot.send_message(
        message.chat.id,
        f"Отправьте новое изображение или /skip, чтобы оставить как есть:"
    )

    if message.text.lower() == "/skip":
        temp_place_data[user_id]["updates"]["description"] = temp_place_data[user_id]["original"]["description"]
        bot.register_next_step_handler(msg, edit_image_step, user_id)
        return

    temp_place_data[user_id]["updates"]["description"] = message.text.strip()
    bot.register_next_step_handler(msg, edit_image_step, user_id)



def edit_image_step(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        start_message(message)
        return

    if message.text and message.text.lower() == "/skip":
        temp_place_data[user_id]["updates"]["img"] = temp_place_data[user_id]["original"]["img"]
        apply_edits(message, user_id)
        return

    if message.photo:
        temp_place_data[user_id]["updates"]["img"] = message.photo[-1].file_id
    else:
        msg = bot.send_message(message.chat.id, "Пожалуйста, отправьте фото или /skip:")
        bot.register_next_step_handler(msg, edit_image_step, user_id)
        return

    apply_edits(message, user_id)



def apply_edits(message, user_id):
    updates = temp_place_data[user_id]["updates"]
    place_id = temp_place_data[user_id]["place_id"]

    db_service.update_place(place_id, updates)
    bot.send_message(message.chat.id, "Место успешно обновлено!")
    del temp_place_data[user_id]  # Очищаем временные данные


@bot.callback_query_handler(func=lambda call: call.data == "delete_place")
def delete_place_callback(call):
    user_id = call.from_user.id
    if not db_service.check_user_role(user_id, "admin"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Введите ID места для удаления:")
    bot.register_next_step_handler(msg, confirm_delete_place)



def confirm_delete_place(message):
    if message.text == "/cancel" or message.text == "/menu":
        start_message(message)
        return
    try:
        place_id = int(message.text)
        db_service.delete_place(place_id)
        bot.send_message(message.chat.id, f"Место {place_id} удалено.")
    except ValueError:
        msg = bot.send_message(message.chat.id, f"Место отсутствует в базе данных\nВведите ID места заново:")
        bot.register_next_step_handler(msg, confirm_delete_place)


@bot.callback_query_handler(func=lambda call: call.data == "delete_user")
def delete_user_callback(call):
    user_id = call.from_user.id
    if not db_service.check_user_role(user_id, "admin"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Введите ID пользователя для удаления:")
    bot.register_next_step_handler(msg, confirm_delete_user)



def confirm_delete_user(message):
    if message.text == "/cancel" or message.text == "/menu":
        start_message(message)
        return
    try:
        user_id = message.text
        a = db_service.find_user(user_id)
        if not a:
            raise ValueError("user_not_found")
        db_service.delete_user()
        bot.send_message(message.chat.id, f"Пользователь {user_id} удален.")
    except ValueError as e:
        error_type = str(e)
        if error_type == "user_not_found":
            error_msg = "Пользователь не найден в базе данных"
        else:
            error_msg = "Неверный формат. Пример: 12345"
        msg = bot.send_message(message.chat.id, f"{error_msg}\nВведите ID заново:")
        bot.register_next_step_handler(msg, confirm_delete_user)


@bot.callback_query_handler(func=lambda call: call.data == "assign_role")
def assign_role_callback(call):
    user_id = call.from_user.id
    if not db_service.check_user_role(user_id, "admin"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "Введите ID пользователя и роль (через пробел):"
    )
    bot.register_next_step_handler(msg, update_user_role)



def update_user_role(message):
    if message.text == "/cancel" or message.text == "/menu":
        start_message(message)
        return
    allowed_roles = {"admin", "user", "manager"}
    try:
        user_id, role = message.text.split()
        # Проверка формата ввода
        if role not in allowed_roles:
            raise ValueError("invalid_role")
        # Проверка существования пользователя
        b = db_service.find_user(user_id)
        if not b:
            raise ValueError("user_not_found")
        curr_role = db_service.get_user_role(user_id)
        if role in curr_role:
            raise ValueError("same_role")

        db_service.change_role(role, user_id)
        bot.send_message(message.chat.id, f"Роль пользователя {user_id} изменена на {role}.")
    except ValueError as e:
        error_type = str(e)
        if error_type == "invalid_role":
            error_msg = f"Неверная роль. Используйте: {', '.join(allowed_roles)}"
        elif error_type == "user_not_found":
            error_msg = "Пользователь не найден в базе данных"
        elif error_type == "same_role":
            error_msg = "У пользователя уже установленна эта роль"
        else:
            error_msg = "Неверный формат. Пример: 12345 manager"
        msg = bot.send_message(message.chat.id, f"{error_msg}\nВведите ID и роль заново:")
        bot.register_next_step_handler(msg, update_user_role)


@bot.callback_query_handler(func=lambda call: call.data == "delete_comment")
def delete_comment_callback(call):
    user_id = call.from_user.id
    if not db_service.check_user_role(user_id, "admin"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "Введите ID пользователя и ID места для удаления комментария (через пробел):"
    )
    bot.register_next_step_handler(msg, confirm_delete_comment)



def confirm_delete_comment(message):
    if message.text == "/cancel" or message.text == "/menu":
        start_message(message)
        return
    try:
        user_id, place_id = map(int, message.text.split())
        c = db_service.find_user(user_id)
        if not c:
            raise ValueError("user_not_found")
        k = db_service.find_place(place_id)
        if not k:
            raise ValueError("place_not_found")
        db_service.delete_comment(user_id, place_id)
        bot.send_message(message.chat.id, "Комментарий удален.")
    except ValueError as e:
        error_type = str(e)
        if error_type == "user_not_found":
            error_msg = "Пользователь не найден в базе данных"
        elif error_type == "place_not_found":
            error_msg = "Место не найдено в базе данных"
        else:
            error_msg = "Неверный формат. Пример: 123 456"
        msg = bot.send_message(message.chat.id, f"{error_msg}\nВведите ID пользователя и ID места заново:")
        bot.register_next_step_handler(msg, confirm_delete_comment)

