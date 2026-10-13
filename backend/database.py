import os
import sqlite3


# 数据库文件路径（基于本文件定位，避免在不同目录启动时又建一个新库）

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats.db")



# 初始化数据库

def init_db():

    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        title TEXT

    )
    """)



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        chat_id INTEGER,

        role TEXT,

        content TEXT

    )
    """)



    conn.commit()

    conn.close()





# 创建聊天

def create_chat(title):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "INSERT INTO chats(title) VALUES(?)",

        (title,)

    )


    chat_id=cursor.lastrowid


    conn.commit()

    conn.close()


    return chat_id






# 获取聊天列表

def get_chats():


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "SELECT * FROM chats ORDER BY id DESC"

    )


    data=cursor.fetchall()


    conn.close()


    return data






# 修改聊天标题

def update_chat_title(chat_id,title):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        UPDATE chats

        SET title=?

        WHERE id=?

        """,

        (
            title,
            chat_id
        )

    )


    conn.commit()

    conn.close()






# 删除聊天

def delete_chat(id):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "DELETE FROM chats WHERE id=?",

        (id,)

    )


    cursor.execute(

        "DELETE FROM messages WHERE chat_id=?",

        (id,)

    )


    conn.commit()

    conn.close()







# 保存消息

def save_message(chat_id,role,content):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        INSERT INTO messages

        (chat_id,role,content)

        VALUES(?,?,?)

        """,

        (
            chat_id,
            role,
            content
        )

    )



    conn.commit()

    conn.close()








# 获取历史消息

def get_messages(chat_id):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        SELECT *

        FROM messages

        WHERE chat_id=?

        ORDER BY id

        """,

        (chat_id,)

    )


    data=cursor.fetchall()


    conn.close()


    return data