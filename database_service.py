import sqlite3

class DatabaseService:
    def __init__(self):
        self.conn = sqlite3.connect(r"database.db3", check_same_thread=False)
        self.cursor = self.conn.cursor()

    # Работа с пользователями
    def add_user(self, user_id, username=None):
        self.cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        self.cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        self.conn.commit()

    def save_query(self, user_id, query):
        self.cursor.execute("UPDATE users SET last_query = ? WHERE user_id = ?", (query, user_id))
        self.conn.commit()

    def get_user_role(self, user_id):
        self.cursor.execute("SELECT user_role FROM users WHERE user_id=?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def check_user_role(self, user_id, required_role):
        role = self.get_user_role(user_id)
        return role == required_role

    def update_user_role(self, user_id, new_role):
        self.cursor.execute("UPDATE users SET user_role=? WHERE user_id=?", (new_role, user_id))
        self.conn.commit()

    def delete_user(self, user_id):
        self.cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        self.cursor.execute("DELETE FROM user_places WHERE user_id=?", (user_id,))
        self.conn.commit()

    # Работа с местами
    def place_exists(self, place_name):
        self.cursor.execute("SELECT place_name FROM places")
        places = [str(x[0]).lower() for x in self.cursor.fetchall()]
        return place_name.lower() in places

    def add_place_to_db(self, place_data, photo_id=None):
        self.cursor.execute("""
            INSERT INTO places (key, place_name, description, img, feedback, count_user, sum_feedback)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            place_data["key"].lower(),
            place_data["name"],
            place_data["description"],
            photo_id,
            0,  # feedback
            0,  # count_user
            0   # sum_feedback
        ))
        self.conn.commit()

    def get_place_by_name(self, place_name):
        self.cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (place_name,))
        return self.cursor.fetchone()

    def get_place_by_id(self, place_id):
        self.cursor.execute("SELECT place_id, place_name, description, img FROM places WHERE place_id=?", (place_id,))
        return self.cursor.fetchone()


    def update_place(self, place_id, updates):
        for field, value in updates.items():
            self.cursor.execute(f"UPDATE places SET {field}=? WHERE rowid=?", (value, place_id))
        self.conn.commit()

    def delete_place(self, place_id):
        self.cursor.execute("DELETE FROM places WHERE rowid=?", (place_id,))
        self.conn.commit()

    # Работа с посещениями
    def user_visited_place(self, user_id, place_id):
        self.cursor.execute(
            "SELECT 1 FROM user_places WHERE user_id = ? AND place_id = ?",
            (user_id, place_id)
        )
        return bool(self.cursor.fetchone())

    def mark_place_visited(self, user_id, place_name=None, place_id=None, feedback=None, comment=None, sentiment=None):
    # Получаем place_id по названию, если он не передан
        if place_id is None:
            place_row = self.cursor.execute("SELECT rowid FROM places WHERE place_name = ?", (place_name,)).fetchone()
            if not place_row:
                return False
            place_id = place_row[0]

        # Обновляем статистику места
        self.cursor.execute("SELECT count_user, sum_feedback FROM places WHERE rowid=?", (place_id,))
        count_user, sum_feedback = self.cursor.fetchone()
        count_user = count_user + 1 if count_user else 1
        sum_feedback = sum_feedback + feedback if sum_feedback else feedback
        avg_feedback = round(sum_feedback / count_user, 1)

        self.cursor.execute(
            "UPDATE places SET count_user=?, sum_feedback=?, feedback=? WHERE rowid=?",
            (count_user, sum_feedback, avg_feedback, place_id)
        )

        # Добавляем запись о посещении
        self.cursor.execute(
            "INSERT INTO user_places (user_id, place_id, comment_user, sentiment, feedback2) VALUES (?, ?, ?, ?, ?)",
            (user_id, place_id, comment, sentiment, feedback)
        )
        self.conn.commit()
        return True

    def get_visited_places(self, user_id):
        self.cursor.execute("""
            SELECT p.place_name, p.feedback, p.description, p.img 
            FROM places p 
            JOIN user_places up ON p.rowid = up.place_id 
            WHERE up.user_id = ? 
            ORDER BY feedback DESC
        """, (user_id,))
        return self.cursor.fetchall()

    # Поиск мест
    def search_places(self, query):
        self.cursor.execute("SELECT key FROM places")
        keys = set(self.cursor.fetchall())
        self.cursor.execute("SELECT place_name FROM places")
        names = set(self.cursor.fetchall())

        results = []

        for key in keys:
            if str(key[0]).lower() == query.lower():
                self.cursor.execute(
                    "SELECT place_name, feedback, description, img FROM places WHERE key = ? ORDER BY feedback DESC",
                    (key[0],)
                )
                results.extend(self.cursor.fetchall())

        if not results:
            for name in names:
                if str(name[0]).lower() == query.lower():
                    self.cursor.execute(
                        "SELECT place_name, feedback, description, img FROM places WHERE place_name = ? ORDER BY feedback DESC",
                        (name[0],)
                    )
                    results.extend(self.cursor.fetchall())

        return results

    # Комментарии
    def has_next(self, place_id, offset):
        self.cursor.execute("""
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
        return self.cursor.fetchone()[0] > 0


    def get_total_comments(self, place_id):
        self.cursor.execute("""
            SELECT COUNT(*) 
            FROM user_places 
            WHERE place_id = ? AND comment_user IS NOT NULL AND comment_user != ''
        """, (place_id,))
        return self.cursor.fetchone()[0]

    def get_next_non_empty_comment(self, place_id, offset):
        self.cursor.execute("""
            SELECT up.comment_user, up.user_id, u.username, up.sentiment, up.feedback2 
            FROM user_places up 
            LEFT JOIN users u ON up.user_id = u.user_id 
            WHERE up.place_id = ? AND up.comment_user IS NOT NULL AND up.comment_user != '' 
            LIMIT 1 OFFSET ?
        """, (place_id, offset))
        return self.cursor.fetchone()

    def delete_comment(self, user_id, place_id):
        self.cursor.execute(
            "DELETE FROM user_places WHERE user_id = ? AND place_id = ?", (user_id, place_id)
        )
        self.conn.commit()

    def find_user(self, us_id):
        self.cursor.execute("SELECT 1 FROM users WHERE user_id=?", (us_id,))
        return self.cursor.fetchone()

    def change_role(self, role, us_id):
        self.cursor.execute("UPDATE users SET user_role=? WHERE user_id=?", (role, us_id))
        self.conn.commit()

    def find_place(self, place_id):
        self.cursor.execute("SELECT 1 FROM places WHERE place_id=?", (place_id,))
        return self.cursor.fetchone()