import sqlite3
import telebot
from config import *
import random
from randoms import *
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from openai import OpenAI

bot = telebot.TeleBot(f"{TOKEN}")
conn = sqlite3.connect(r"database.db3", check_same_thread=False)
cursor = conn.cursor()

# Глобальные переменные
user_results = {}
temp_place_data = {}  # Для хранения данных о новых местах в процессе добавления
client = OpenAI(
    api_key=f"{KEY}",
    base_url=f"{URL}",
)


def analyze_comment(comment):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "Оцени тональность комментария. Ответь ТОЛЬКО одним словом: 'хороший' или 'плохой'."
                },
                {
                    "role": "user",
                    "content": comment
                }
            ],
            temperature=0.0  # Для минимизации случайности
        )
        return response.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"Ошибка анализа: {e}")
        return "нейтральный"

def check_user_role(user_id, required_role):
    with conn:  # Автоматически завершает транзакцию
        cur = conn.cursor()
        cur.execute("SELECT user_role FROM users WHERE user_id=?", (user_id,))
        result = cur.fetchone()
        return result[0] == required_role if result else False


# РАБОТА С ТГ КАНАЛОМ
CHANNEL_ID = -1002591278253


@bot.channel_post_handler(content_types=['text', 'photo'])
def handle_channel_post(message):
    if message.chat.id != CHANNEL_ID:
        return

    # Получаем данные из сообщения
    if message.photo:
        photo_id = message.photo[-1].file_id
        text = message.caption if message.caption else ""
    else:
        photo_id = None
        text = message.text

    # Парсим информацию о месте
    place_data = parse_place_info(text)

    # Если формат неправильный - отправляем инструкцию
    if not place_data:
        bot.send_message(message.chat.id, "Введите место в формате:\n"
                                          "Название: Пример\n"
                                          "Описание: Пример\n"
                                          "Ключ: Пример")
        return

    # Проверяем, существует ли уже такое место
    cursor.execute("SELECT place_name FROM places")
    places = [x[0].lower() for x in cursor.fetchall()]
    if place_data["name"].lower() in places:
        bot.send_message(message.chat.id,
                         "Такое место уже существует! Пожалуйста, отправьте новое сообщение с другим названием.")
        return

    # Если все хорошо - добавляем в БД
    add_place_to_db(place_data, photo_id)
    bot.send_message(message.chat.id, f"Место '{place_data['name']}' успешно добавлено!")


def parse_place_info(text):
    data = {}
    required_fields = ['название', 'описание', 'ключ']
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    for line in lines:
        # Разделяем строку на ключ и значение
        if ':' not in line:
            continue

        key_part, value_part = line.split(':', 1)
        key = key_part.strip().lower()
        value = value_part.strip()

        if key in required_fields:
            # Сохраняем данные с правильными ключами
            if key == 'название':
                data['name'] = value
            elif key == 'описание':
                data['description'] = value
            elif key == 'ключ':
                data['key'] = value

    # Проверяем, все ли обязательные поля заполнены
    if all(field in data for field in ['name', 'description', 'key']):
        return data
    return None


def add_place_to_db(place_data, photo_id=None):
    """Добавляет место в базу данных"""
    cursor.execute(
        """INSERT INTO places 
        (key, place_name, description, img) 
        VALUES (?, ?, ?, ?)""",
        (
            place_data['key'].lower(),
            place_data['name'],
            place_data['description'],
            photo_id
        )
    )
    conn.commit()


# КОНЕЦ РАБОТЫ С ТГ КАНАЛОМ

def db_table_val(user_id: int, username):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()


def save_user_query(user_id, query):
    cursor.execute(
        "UPDATE users SET last_query = ? WHERE user_id = ?", (query, user_id)
    )
    conn.commit()


@bot.callback_query_handler(func=lambda call: call.data.startswith(("next2_", "prev2_")))
def handle_comment_pagination(call):
    user_id = call.from_user.id
    results = user_results.get(user_id)
    # Получаем ID места
    cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (results[user_results["index"]][0],))
    place_id = cursor.fetchone()[0]
    # Определяем направление пагинации
    if call.data.startswith("next2_"):
        offset = int(call.data.split("_")[1])
    else:
        offset = int(call.data.split("_")[1])
    # Получаем следующий/предыдущий непустой комментарий
    cursor.execute(
        "SELECT up.comment_user, up.user_id, u.username, up.sentiment, up.feedback2 FROM user_places up LEFT JOIN users u ON up.user_id = u.user_id WHERE up.place_id = ? AND up.comment_user IS NOT NULL AND up.comment_user != '' LIMIT 1 OFFSET ?",
        (place_id, offset))
    comment_data = cursor.fetchone()
    if comment_data:
        comment_text, comment_user_id, username, sentiment, feedback = comment_data
        markup = InlineKeyboardMarkup()
        buttons = []
        # Проверяем, есть ли предыдущие комментарии
        if offset > 0:
            buttons.append(InlineKeyboardButton("Назад", callback_data=f"prev2_{offset - 1}"))
        # Проверяем, есть ли следующие комментарии
        cursor.execute("""
            SELECT COUNT(*) 
            FROM user_places 
            WHERE place_id = ? 
            AND comment_user IS NOT NULL
            AND comment_user != ''
            AND rowid > (
                SELECT rowid 
                FROM user_places 
                WHERE place_id = ? 
                AND comment_user IS NOT NULL
                AND comment_user != ''
                LIMIT 1 OFFSET ?
            )
        """, (place_id, place_id, offset))
        has_next = cursor.fetchone()[0] > 0
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


def search_places(query):
    cursor.execute("SELECT key FROM places")
    keys = set(cursor.fetchall())
    cursor.execute("SELECT place_name FROM places")
    a = set(cursor.fetchall())
    results = []
    for key in keys:
        if str(key[0]).lower() == query.lower():
            cursor.execute(
                "SELECT place_name, feedback, description, img FROM places WHERE key = ? ORDER BY feedback DESC",
                (key[0],),
            )
            results.extend(cursor.fetchall())
    if not results:
        for place_name in a:
            if str(place_name[0]).lower() == query.lower():
                cursor.execute(
                    "SELECT place_name, feedback, description, img FROM places WHERE place_name = ? ORDER BY feedback DESC",
                    (place_name[0],),
                )
                results.extend(cursor.fetchall())
    return results


@bot.callback_query_handler(func=lambda call: call.data == "comments")
def comments_callback(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    results = user_results.get(user_id)
    cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (results[user_results["index"]][0],))
    place_id = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM user_places WHERE place_id = ? AND comment_user IS NOT NULL AND comment_user != ''",
        (place_id,))
    total_comments = cursor.fetchone()[0]
    user_results["number"] = total_comments
    user_results["comment_index"] = 0
    if total_comments > 0:
        # Находим первый непустой комментарий
        offset = 0
        while True:
            cursor.execute(
                "SELECT up.comment_user, up.user_id, u.username, up.sentiment, up.feedback2 FROM user_places up LEFT JOIN users u ON up.user_id = u.user_id WHERE up.place_id = ? AND up.comment_user IS NOT NULL AND up.comment_user != '' LIMIT 1 OFFSET ?",
                (place_id, offset))
            comment_data = cursor.fetchone()
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


@bot.message_handler(commands=["start"])
def start_message(message):
    markup = InlineKeyboardMarkup()
    user_id = message.from_user.id
    markup.add(InlineKeyboardButton("🔍 Найти новые места", callback_data="search"))
    markup.add(InlineKeyboardButton("📌 Показать места, где был", callback_data="show"))
    markup.add(InlineKeyboardButton("➕ Добавить место", callback_data="add_user_place"))

    if check_user_role(user_id, "admin") or check_user_role(user_id, "manager"):
        markup.add(InlineKeyboardButton("Добавить место(mod)", callback_data="add_place"))
        markup.add(InlineKeyboardButton("Редактировать место(mod)", callback_data="edit_place"))

    if check_user_role(user_id, "admin"):
        markup.add(InlineKeyboardButton("Удалить место(mod)", callback_data="delete_place"))
        markup.add(InlineKeyboardButton("Удалить пользователя(mod)", callback_data="delete_user"))
        markup.add(InlineKeyboardButton("Удалить комментарий(mod)", callback_data="delete_comment"))
        markup.add(InlineKeyboardButton("Изменить роль(mod)", callback_data="assign_role"))
    greeting = random.choice(GREETINGS)
    bot.send_message(
        message.chat.id,
        f"{greeting}\n\nЯ ваш помощник в поиске интересных мест. Что вас интересует?",
        reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "show")
def show_callback(call):
    us_id = call.from_user.id
    cursor.execute(
        "SELECT p.place_name, p.feedback, p.description, p.img FROM places p JOIN user_places up ON p.rowid = up.place_id WHERE up.user_id = ? ORDER BY feedback DESC",
        (us_id,),
    )
    visited_places = cursor.fetchall()
    if visited_places:
        user_results[us_id] = visited_places
        send_result(call.message.chat.id, us_id, 0)
    else:
        bot.send_message(call.message.chat.id, "Похоже, вы еще не отмечали посещенные места. 🧐")


@bot.message_handler(commands=["menu"])
def start_message(message):
    markup = InlineKeyboardMarkup()
    user_id = message.from_user.id
    markup.add(InlineKeyboardButton("🔍 Найти новые места", callback_data="search"))
    markup.add(InlineKeyboardButton("📌 Показать места, где был", callback_data="show"))
    markup.add(InlineKeyboardButton("➕ Добавить место", callback_data="add_user_place"))

    if check_user_role(user_id, "admin") or check_user_role(user_id, "manager"):
        markup.add(InlineKeyboardButton("Добавить место(mod)", callback_data="add_place"))
        markup.add(InlineKeyboardButton("Редактировать место(mod)", callback_data="edit_place"))

    if check_user_role(user_id, "admin"):
        markup.add(InlineKeyboardButton("Удалить место(mod)", callback_data="delete_place"))
        markup.add(InlineKeyboardButton("Удалить пользователя(mod)", callback_data="delete_user"))
        markup.add(InlineKeyboardButton("Удалить комментарий(mod)", callback_data="delete_comment"))
        markup.add(InlineKeyboardButton("Изменить роль(mod)", callback_data="assign_role"))

    bot.send_message(message.chat.id, "<b>🎀ГЛАВНОЕ МЕНЮ🎀</b>", reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "point")
def point_callback(call):
    user_id = call.from_user.id
    results = user_results.get(user_id)
    place_name = results[user_results["index"]][0]
    cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (place_name,))
    place_row = cursor.fetchone()
    place_id = place_row[0]

    # Проверяем, есть ли уже запись о посещении
    cursor.execute(
        "SELECT 1 FROM user_places WHERE user_id = ? AND place_id = ?",
        (user_id, place_id),
    )
    if cursor.fetchone():
        bot.send_message(call.message.chat.id, "Вы уже отмечали это место как посещенное. 😊")
    else:
        msg = bot.send_message(
            call.message.chat.id,
            "Поделитесь, пожалуйста, вашими впечатлениями об этом месте. 🌟\n"
            "Как бы вы оценили его от 0 до 10? (10 - это потрясающе!)")
        bot.register_next_step_handler(msg, point_db0, user_id)


def point_db0(message, us_id):
    results = user_results.get(us_id)
    if not results:
        bot.send_message(message.chat.id, "ОШИБКА")
    place_name = results[user_results["index"]][0]
    feedback = message.text
    try:
        feedback_int = int(feedback)
        if feedback_int < 0 or feedback_int > 10:
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Кажется, вы ввели что-то не то. 😅\n"
                                                "Пожалуйста, введите целое число от 0 до 10:")
        bot.register_next_step_handler(msg, point_db0, us_id)
        return
    cursor.execute("SELECT count_user FROM places WHERE place_name = ?", (place_name,))
    a = cursor.fetchone()[0]
    if a:
        count_user = a + 1
    else:
        count_user = 1
    cursor.execute("SELECT sum_feedback FROM places WHERE place_name = ?", (place_name,))
    b = cursor.fetchone()[0]
    if b:
        sum_feedback = b + feedback_int
    else:
        sum_feedback = feedback_int
    cursor.execute(
        "UPDATE places SET count_user = ?, sum_feedback = ?, feedback = ? WHERE place_name = ? ",
        (count_user, sum_feedback, round(sum_feedback / count_user, 1), place_name))
    conn.commit()
    msg = bot.send_message(message.chat.id,
                           "Спасибо за оценку! 💖\n"
                           "Не хотите ли поделиться своими впечатлениями в комментарии?\n"
                           "(или нажмите /skip если не хотите оставлять комментарий)")
    bot.register_next_step_handler(msg, point_db, us_id, feedback_int)


def point_db(message, us_id, feedback_int):
    results = user_results.get(us_id)
    if message.text and message.text.lower() == "/skip":
        comment = None
        sentiment = None
        bot.send_message(
            message.chat.id,
            "Хорошо, комментарий не добавлен. 😊 Больше вы не сможете добавить комментарий к этому месту 😫")
    else:
        comment = message.text
        sentiment = analyze_comment(message.text)
        bot.send_message(
            message.chat.id,
            "Спасибо за ваш отзыв! 🙏\n"
            "Ваше мнение очень важно для нас и других пользователей."
        )
    place_name = results[user_results["index"]][0]
    # Находим place_id по названию места
    cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (place_name,))
    place_row = cursor.fetchone()
    if not place_row:
        bot.send_message(message.chat.id, "ОШИБКА: Место не найдено")
        return
    place_id = place_row[0]
    # Проверяем, есть ли уже запись о посещении
    cursor.execute(
        "SELECT 1 FROM user_places WHERE user_id = ? AND place_id = ?",
        (us_id, place_id)
    )
    cursor.execute(
        "INSERT INTO user_places (user_id, place_id, comment_user, sentiment, feedback2) VALUES (?, ?, ?, ?, ?)",
        (us_id, place_id, comment, sentiment, feedback_int)
    )
    conn.commit()
    bot.send_message(message.chat.id, "Место добавлено в посещённые 😊")


@bot.callback_query_handler(func=lambda call: call.data == "search")
def search_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
                     "Что бы вы хотели найти? 🔍\n"
                     "Можете ввести название места или ключевое слово.\n"
                     "Например: 'прогулка', 'театр', 'музей', 'ресторан'")


@bot.callback_query_handler(func=lambda call: call.data == "add_user_place")
def add_user_place_callback(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    # Инициализация данных для нового места
    temp_place_data[user_id] = {"chat_id": call.message.chat.id, "data": {}}
    msg = bot.send_message(call.message.chat.id, "Отлично! Давайте добавим новое место. 😺\n"
                                                 "Если захотите прервать добавление места, то просто нажмите /cancel\n"
                                                 "Для начала, как называется ваше место?")
    bot.register_next_step_handler(msg, process_place_name, user_id)


def process_place_name(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        cursor.execute("SELECT place_name FROM places")
        places = [str(x[0]).lower() for x in cursor.fetchall()]
        if message.text.lower() in places:
            bot.send_message(
                message.chat.id, "Такое место уже есть в нашей базе, попробуйте добавить другое 😊\n")
            msg = bot.send_message(message.chat.id, "Попробуйте ввести другое название:")
            bot.register_next_step_handler(msg, process_place_name, user_id)
        else:
            temp_place_data[user_id]["data"]["name"] = message.text
            msg = bot.send_message(
                message.chat.id, "Как бы вы оценили это место по шкале от 1 до 10? 🌟\n"
                                 "(1 - совсем не понравилось, 10 - просто потрясающе!)")
            bot.register_next_step_handler(msg, process_comment, user_id)


def process_comment(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        try:
            # Пытаемся преобразовать введенный текст в число
            feedback = int(message.text)
            # Проверяем, что число в допустимом диапазоне
            if feedback < 1 or feedback > 10:
                raise ValueError("Оценка должна быть от 1 до 10")
            # Если все хорошо, сохраняем оценку
            count_user = 1
            sum_feedback = feedback
            temp_place_data[user_id]["data"]["sum_feedback"] = sum_feedback
            temp_place_data[user_id]["data"]["count_user"] = count_user
            temp_place_data[user_id]["data"]["feedback"] = round(sum_feedback / count_user, 1)
            # Запрашиваем комментарий
            msg = bot.send_message(
                message.chat.id,
                "Введите комментарий о месте (или нажмите /skip чтобы пропустить):"
            )
            bot.register_next_step_handler(msg, process_place_feedback, user_id)
        except ValueError as e:
            # Обрабатываем ошибки преобразования или неверного диапазона
            error_msg = "Пожалуйста, введите целое число от 1 до 10"
            if str(e) == "Оценка должна быть от 1 до 10":
                error_msg = str(e)
            # Повторно запрашиваем оценку
            msg = bot.send_message(message.chat.id, error_msg)
            bot.register_next_step_handler(msg, process_comment, user_id)


def process_place_feedback(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        if message.text.lower() == "/skip":
            temp_place_data[user_id]["data"]["comment"] = None
            temp_place_data[user_id]["data"]["sentiment"] = None

        else:
            temp_place_data[user_id]["data"]["comment"] = message.text
            temp_place_data[user_id]["data"]["sentiment"] = analyze_comment(message.text)
        msg = bot.send_message(message.chat.id, "Введите описание места:")
        bot.register_next_step_handler(msg, process_place_description, user_id)


def process_place_description(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        temp_place_data[user_id]["data"]["description"] = message.text
        msg = bot.send_message(
            message.chat.id, "Введите тэг(ключ), по которому можно найти это место:"
        )
        bot.register_next_step_handler(msg, process_keys, user_id)


def process_keys(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        temp_place_data[user_id]["data"]["key"] = message.text.lower()
        msg = bot.send_message(
            message.chat.id, "Отправьте фото места (или нажмите /skip чтобы пропустить):"
        )
        bot.register_next_step_handler(msg, process_place_photo, user_id)


def process_place_photo(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        bot.send_message(message.chat.id, "Хорошо, добавление места прервано")
        start_message(message)
        return
    else:
        chat_id = temp_place_data[user_id]["chat_id"]
        data = temp_place_data[user_id]["data"]
        if message.photo:
            data["img"] = message.photo[-1].file_id
        elif message.text and message.text.lower() == "/skip":
            data["img"] = None
        else:
            msg = bot.send_message(chat_id, "Пожалуйста, отправьте фото или нажмите /skip")
            bot.register_next_step_handler(msg, process_place_photo, user_id)
            return

        cursor.execute(
            "INSERT INTO places (key, place_name, feedback, count_user, sum_feedback, description, img) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                data["key"],
                data["name"],
                data["feedback"],
                data["count_user"],
                data["sum_feedback"],
                data["description"],
                data.get("img"),
            ),
        )
        responses = random.choice(RESPONSES)
        conn.commit()
        bot.send_message(
            chat_id,
            f"{responses}",
        )
        cursor.execute(
            "SELECT rowid FROM places WHERE place_name = ?",
            (temp_place_data[user_id]["data"]["name"],),
        )
        place_row = cursor.fetchone()
        place_id = place_row[0]
        cursor.execute(
            "INSERT INTO user_places (user_id, place_id, comment_user, sentiment, feedback2) VALUES (?, ?, ?, ?, ?)",
            (user_id, place_id, temp_place_data[user_id]["data"]["comment"], data["sentiment"], data["sum_feedback"])
        )
        conn.commit()
        # Очищаем временные данные
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        # Возвращаем в главное меню
        bot.send_message(chat_id, "Что дальше?")
        start_message(message)


@bot.message_handler(content_types=["text"])
def get_text_message(message):
    us_id = message.from_user.id
    username = message.from_user.username
    db_table_val(us_id, username)
    user_text = message.text
    save_user_query(us_id, user_text)
    user_results[us_id] = None
    # Проверяем, не находится ли пользователь в процессе добавления места
    if us_id in temp_place_data:
        bot.send_message(
            message.chat.id, "⚠️ Завершите действие или нажмите /cancel"
        )
        return
    results = search_places(user_text)
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


@bot.callback_query_handler(func=lambda call: call.data == "add_place")
def add_place_callback(call):
    user_id = call.from_user.id
    if check_user_role(user_id, "user"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    if user_id in temp_place_data:
        del temp_place_data[user_id]
    bot.answer_callback_query(call.id)
    prompt = (
        "Отправьте фотографию места (опционально), введите данные о месте в формате:\n"
        "Название: Пример\n"
        "Описание: Пример\n"
        "Ключ: Пример"
    )
    msg = bot.send_message(call.message.chat.id, prompt)
    bot.register_next_step_handler(msg, process_place_input)


def process_place_input(message):
    if message.text == "/cancel" or message.text == "/menu":
        start_message(message)
        return
    # Получаем текст и изображение
    if message.photo:
        photo_id = message.photo[-1].file_id
        text = message.caption if message.caption else ""
    else:
        photo_id = None
        text = message.text

    # Парсим данные
    place_data = parse_place_info(text)
    if not place_data or text == "":
        msg = bot.send_message(
            message.chat.id,
            "Введите данные в правильном формате:\n"
            "Название: Пример\n"
            "Описание: Пример\n"
            "Ключ: пример"
        )
        bot.register_next_step_handler(msg, process_place_input)
        return

    # Проверяем дубликаты
    cursor.execute("SELECT place_name FROM places")
    places = [str(x[0]).lower() for x in cursor.fetchall()]
    if place_data["name"].lower() in places:
        msg = bot.send_message(message.chat.id, "Такое место уже существует! Введите данные заново:")
        bot.register_next_step_handler(msg, process_place_input)
        return

    # Добавляем место в БД
    add_place_to_db(place_data, photo_id)
    bot.send_message(message.chat.id, f"Место '{place_data['name']}' успешно добавлено!")


@bot.callback_query_handler(func=lambda call: call.data == "edit_place")
def edit_place_callback(call):
    user_id = call.from_user.id
    if check_user_role(user_id, "user"):
        bot.answer_callback_query(call.id, "⚠️ Недостаточно прав")
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    if user_id in temp_place_data:
        del temp_place_data[user_id]
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Введите ID места для редактирования:")
    bot.register_next_step_handler(msg, select_place_for_edit, user_id)


def select_place_for_edit(message, user_id):
    if message.text == "/cancel" or message.text == "/menu":
        if user_id in temp_place_data:
            del temp_place_data[user_id]
        start_message(message)
        return
    place_id = message.text
    cursor.execute("SELECT place_id, place_name, description, img FROM places WHERE place_id=?", (place_id,))
    place = cursor.fetchone()
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
            f"Текущее описание: {temp_place_data[user_id]["original"]["description"]}\n"
            f"Введите новое описание или /skip, чтобы оставить как есть:"
        )
        bot.register_next_step_handler(msg, edit_description_step, user_id)
        return

    new_name = message.text.strip()
    cursor.execute("SELECT place_name FROM places WHERE place_name=?", (new_name,))
    if cursor.fetchone():
        msg = bot.send_message(message.chat.id, "Такое название уже занято. Попробуйте другое:")
        bot.register_next_step_handler(msg, edit_name_step, user_id)
    else:
        temp_place_data[user_id]["updates"]["name"] = new_name
        msg = bot.send_message(
            message.chat.id,
            f"Текущее описание: {temp_place_data[user_id]["original"]["description"]}\n"
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

    # Обновляем только изменённые поля
    if "name" in updates:
        cursor.execute("UPDATE places SET place_name=? WHERE place_id=?", (updates["name"], place_id))
    if "description" in updates:
        cursor.execute(
            "UPDATE places SET description=? WHERE place_id=?", (updates["description"], place_id))
    if "img" in updates:
        cursor.execute("UPDATE places SET img=? WHERE place_id=?", (updates["img"], place_id))

    conn.commit()
    bot.send_message(message.chat.id, "Место успешно обновлено!")
    del temp_place_data[user_id]  # Очищаем временные данные


@bot.callback_query_handler(func=lambda call: call.data == "delete_place")
def delete_place_callback(call):
    user_id = call.from_user.id
    if not check_user_role(user_id, "admin"):
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
        place_id = message.text
        cursor.execute("SELECT place_id FROM places WHERE place_id=?", (place_id,))
        if not cursor.fetchone():
            raise ValueError
        cursor.execute("SELECT place_name FROM places WHERE place_id=?", (place_id,))
        place_name = cursor.fetchone()
        cursor.execute("DELETE FROM places WHERE place_id=?", (place_id,))
        conn.commit()
        bot.send_message(message.chat.id, f"Место {place_name} удалено.")
    except ValueError:
        msg = bot.send_message(message.chat.id, f"Место отсутствует в базе данных\nВведите ID места заново:")
        bot.register_next_step_handler(msg, confirm_delete_place)


@bot.callback_query_handler(func=lambda call: call.data == "delete_user")
def delete_user_callback(call):
    user_id = call.from_user.id
    if not check_user_role(user_id, "admin"):
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
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            raise ValueError("user_not_found")
        cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        cursor.execute("DELETE FROM user_places WHERE user_id=?", (user_id,))
        conn.commit()
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
    if not check_user_role(user_id, "admin"):
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
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            raise ValueError("user_not_found")
        cursor.execute("SELECT user_role FROM users WHERE user_id=?", (user_id,))
        curr_role = cursor.fetchone()
        if role in curr_role:
            raise ValueError("same_role")
        cursor.execute("UPDATE users SET user_role=? WHERE user_id=?", (role, user_id))
        conn.commit()
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
    if not check_user_role(user_id, "admin"):
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
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            raise ValueError("user_not_found")
        cursor.execute("SELECT 1 FROM places WHERE place_id=?", (place_id,))
        if not cursor.fetchone():
            raise ValueError("place_not_found")
        cursor.execute(
            "DELETE FROM user_places WHERE user_id = ? AND place_id = ?", (user_id, place_id))
        conn.commit()
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


if __name__ == "__main__":
    print("Бот запущен...")
    bot.polling(none_stop=True)
