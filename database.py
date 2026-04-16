import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )


def get_all_conversations():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.started_at, COUNT(m.id) AS message_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id, c.started_at
        HAVING COUNT(m.id) >= 2
        ORDER BY c.started_at DESC;
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "session_id": str(row[0]),
            "started_at": row[1].strftime("%Y-%m-%d %H:%M"),
            "message_count": row[2],
        }
        for row in rows
    ]


def get_conversations_by_class(class_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.started_at, COUNT(m.id) AS message_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.class_id = %s
        GROUP BY c.id, c.started_at
        HAVING COUNT(m.id) >= 2
        ORDER BY c.started_at DESC;
        """,
        (class_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "session_id": str(row[0]),
            "started_at": row[1].strftime("%Y-%m-%d %H:%M"),
            "message_count": row[2],
        }
        for row in rows
    ]


def get_messages(conversation_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sender, content
        FROM messages
        WHERE conversation_id = %s
        ORDER BY message_index ASC;
        """,
        (conversation_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "sender": sender,
            "content": content,
        }
        for sender, content in rows
    ]


def create_user():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO users DEFAULT VALUES
        RETURNING id;
        """
    )
    user_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return user_id


def create_conversation(user_id, class_id=None):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO conversations (user_id, class_id)
        VALUES (%s, %s)
        RETURNING id;
        """,
        (user_id, class_id)
    )
    conversation_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return conversation_id


def save_message(conversation_id, sender, content, message_index):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO messages (conversation_id, sender, content, message_index)
        VALUES (%s, %s, %s, %s);
        """,
        (conversation_id, sender, content, message_index)
    )

    conn.commit()
    cur.close()
    conn.close()


def save_summary(conversation_id, summary_text):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO summaries (conversation_id, summary_text)
        VALUES (%s, %s);
        """,
        (conversation_id, summary_text)
    )

    conn.commit()
    cur.close()
    conn.close()


def clear_all_conversations():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("TRUNCATE conversations CASCADE;")
    conn.commit()
    cur.close()
    conn.close()