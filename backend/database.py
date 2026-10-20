import os
import sqlite3
import json


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
    CREATE TABLE IF NOT EXISTS agent_runs(
        run_id TEXT PRIMARY KEY,
        chat_id INTEGER,
        question TEXT,
        reply TEXT,
        status TEXT,
        rounds INTEGER,
        duration_ms INTEGER,
        total_tokens INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_steps(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_index INTEGER,
        round_no INTEGER,
        kind TEXT,
        tool TEXT,
        args TEXT,
        ok INTEGER,
        elapsed_ms INTEGER,
        result_preview TEXT,
        result_data TEXT,
        reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_chat ON agent_runs(chat_id)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        category TEXT DEFAULT '偏好',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learning_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        goal TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learning_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        details TEXT DEFAULT '',
        stage TEXT DEFAULT '',
        status TEXT DEFAULT 'todo',
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_learning_tasks_plan ON learning_tasks(plan_id, sort_order, id)")



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


def get_recent_messages(chat_id, limit=12):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, max(1, min(int(limit), 30))),
    )
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


def save_agent_run(chat_id, question, result):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO agent_runs(run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens) VALUES(?,?,?,?,?,?,?,?)",
        (result["run_id"], chat_id, question, result["reply"], result["status"],
         result["rounds"], result["duration_ms"], result["total_tokens"]),
    )
    for index, step in enumerate(result.get("steps", [])):
        cursor.execute(
            "INSERT INTO agent_steps(run_id,step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (result["run_id"], index, step.get("round_no"), step.get("kind"), step.get("tool"),
             json.dumps(step.get("args", {}), ensure_ascii=False),
             None if step.get("ok") is None else int(bool(step.get("ok"))), step.get("elapsed_ms"),
             step.get("result_preview"), json.dumps(step.get("result_data", {}), ensure_ascii=False),
             step.get("reason") or step.get("detail")),
        )
    conn.commit()
    conn.close()


def get_agent_run(run_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,created_at FROM agent_runs WHERE run_id=?", (run_id,))
    run = cursor.fetchone()
    if not run:
        conn.close()
        return None
    cursor.execute("SELECT step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason FROM agent_steps WHERE run_id=? ORDER BY step_index", (run_id,))
    steps = cursor.fetchall()
    conn.close()
    keys = ("step_index", "round_no", "kind", "tool", "args", "ok", "elapsed_ms", "result_preview", "result_data", "reason")
    parsed_steps = []
    for row in steps:
        item = dict(zip(keys, row))
        for key in ("args", "result_data"):
            try:
                item[key] = json.loads(item[key] or "{}")
            except (ValueError, TypeError):
                item[key] = {}
        parsed_steps.append(item)
    return {"run": dict(zip(("run_id", "chat_id", "question", "reply", "status", "rounds", "duration_ms", "total_tokens", "created_at"), run)), "steps": parsed_steps}


def get_agent_runs_for_chat(chat_id, limit=100):
    conn = sqlite3.connect(DB_PATH)
    run_ids = conn.execute(
        "SELECT run_id FROM agent_runs WHERE chat_id=? ORDER BY rowid ASC LIMIT ?",
        (chat_id, max(1, min(int(limit), 200))),
    ).fetchall()
    conn.close()
    results = []
    for (run_id,) in run_ids:
        result = get_agent_run(run_id)
        if result:
            results.append({**result["run"], "steps": result["steps"]})
    return results


def add_memory(content, category="偏好"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO memories(content,category) VALUES(?,?)", (content[:1200], category[:40]))
    memory_id = cursor.lastrowid
    cursor.execute("DELETE FROM memories WHERE id NOT IN (SELECT id FROM memories ORDER BY id DESC LIMIT 100)")
    conn.commit()
    conn.close()
    return memory_id


def list_memories(limit=100):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id,content,category,created_at FROM memories ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    conn.close()
    return rows


def delete_memory(memory_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def save_learning_plan(title, goal, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO learning_plans(title,goal,content) VALUES(?,?,?)", (title[:160], goal[:1000], content[:16000]))
    plan_id = cursor.lastrowid
    cursor.execute("DELETE FROM learning_plans WHERE id NOT IN (SELECT id FROM learning_plans ORDER BY id DESC LIMIT 200)")
    conn.commit()
    conn.close()
    return plan_id


def list_learning_plans(limit=50):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id,title,goal,content,status,created_at FROM learning_plans ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    conn.close()
    return rows


def get_learning_plan(plan_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id,title,goal,content,status,created_at FROM learning_plans WHERE id=?",
        (plan_id,),
    ).fetchone()
    conn.close()
    return row


def set_learning_plan_status(plan_id, status):
    if status not in {"active", "done"}:
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("UPDATE learning_plans SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, plan_id))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_learning_plan(plan_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM learning_tasks WHERE plan_id=?", (plan_id,))
    cursor.execute("DELETE FROM learning_plans WHERE id=?", (plan_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def add_learning_tasks(plan_id, tasks):
    """Store a bounded ordered task list for one plan."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    created = []
    for index, task in enumerate(tasks[:50]):
        title = str(task.get("title", "")).strip()[:180]
        if not title:
            continue
        details = str(task.get("details", "")).strip()[:1200]
        stage = str(task.get("stage", "")).strip()[:80]
        cursor.execute(
            "INSERT INTO learning_tasks(plan_id,title,details,stage,sort_order) VALUES(?,?,?,?,?)",
            (plan_id, title, details, stage, index),
        )
        created.append({"id": cursor.lastrowid, "title": title, "details": details, "stage": stage, "status": "todo"})
    conn.commit()
    conn.close()
    return created


def list_learning_tasks(plan_id):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,title,details,stage,status,sort_order,created_at,completed_at FROM learning_tasks WHERE plan_id=? ORDER BY sort_order,id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return rows


def set_learning_task_status(plan_id, task_id, status):
    if status not in {"todo", "done"}:
        return False
    conn = sqlite3.connect(DB_PATH)
    completed_at = "CURRENT_TIMESTAMP" if status == "done" else "NULL"
    cursor = conn.execute(
        f"UPDATE learning_tasks SET status=?,completed_at={completed_at} WHERE id=? AND plan_id=?",
        (status, task_id, plan_id),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_learning_task(plan_id, task_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM learning_tasks WHERE id=? AND plan_id=?", (task_id, plan_id))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def replace_unfinished_learning_tasks(plan_id, tasks):
    """Keep completed work as a record and replace only unfinished work."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM learning_tasks WHERE plan_id=? AND status='todo'", (plan_id,))
    offset_row = cursor.execute("SELECT COALESCE(MAX(sort_order), -1) FROM learning_tasks WHERE plan_id=?", (plan_id,)).fetchone()
    offset = int(offset_row[0]) + 1
    created = []
    for index, task in enumerate(tasks[:50]):
        title = str(task.get("title", "")).strip()[:180]
        if not title:
            continue
        details = str(task.get("details", "")).strip()[:1200]
        stage = str(task.get("stage", "")).strip()[:80]
        cursor.execute(
            "INSERT INTO learning_tasks(plan_id,title,details,stage,sort_order) VALUES(?,?,?,?,?)",
            (plan_id, title, details, stage, offset + index),
        )
        created.append({"id": cursor.lastrowid, "title": title, "details": details, "stage": stage, "status": "todo"})
    conn.commit()
    conn.close()
    return created
