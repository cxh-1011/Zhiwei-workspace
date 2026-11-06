import os
import sqlite3
import json
import logging
import time


logger = logging.getLogger("ai_robot.database")


# 数据库文件路径（基于本文件定位，避免在不同目录启动时又建一个新库）

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats.db")

# 备份目录在项目根的 backups/db 下，和代码回滚快照分开放
BACKUP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups", "db"
)
BACKUP_KEEP = 7



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

    # 课程进度。课程正文放文件（能 git、能手改），只有「学到哪了」这种
    # 随时会变的状态放这里。mastery 用 0-3 而不是布尔：同样标了「学完」，
    # 有人是看懂了，有人是能自己写出来，这两种不该推荐同一章。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS course_progress(
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        status TEXT DEFAULT 'todo',
        mastery INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (course_id, chapter_id)
    )
    """)

    # 章节里问过什么。用来让导师知道「他在这章卡过什么」，
    # 也让「推荐下一步」有依据，而不是每次都从第一章重新开始猜。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS course_questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_questions_ch ON course_questions(course_id, chapter_id)")



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        chat_id INTEGER,

        role TEXT,

        content TEXT

    )
    """)



    # ---- 模型服务商配置表 ----
    # 表结构由 model_store 维护，这里同步建一次，保证 /models 第一次调用就能读写。
    from model_store import PROVIDER_SCHEMA

    cursor.execute(PROVIDER_SCHEMA)

    # ---- 兼容旧库的增量迁移 ----
    # memories 新增 source 列：区分「用户手动添加」和「助手写入」，
    # 让用户能审计、能只删助手加的那些。旧库没有这列，这里补上。
    _ensure_column(cursor, "memories", "source", "TEXT DEFAULT 'user'")

    # agent_runs 记录本次用的服务商与模型，切换 API 后能回溯「这条回答是谁答的」
    _ensure_column(cursor, "agent_runs", "provider", "TEXT")
    _ensure_column(cursor, "agent_runs", "model", "TEXT")

    # agent_steps 记录每一轮的思考摘要，回看历史时能还原「模型当时怎么想的」
    _ensure_column(cursor, "agent_steps", "reasoning", "TEXT")

    conn.commit()

    conn.close()


def _ensure_column(cursor, table, column, ddl):
    """给已存在的表补列；CREATE TABLE IF NOT EXISTS 不会改动旧表结构。"""
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(%s)" % table).fetchall()}
    if column not in existing:
        cursor.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))





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
        "INSERT OR REPLACE INTO agent_runs(run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,provider,model) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (result["run_id"], chat_id, question, result["reply"], result["status"],
         result["rounds"], result["duration_ms"], result["total_tokens"],
         result.get("provider"), result.get("model")),
    )
    for index, step in enumerate(result.get("steps", [])):
        cursor.execute(
            "INSERT INTO agent_steps(run_id,step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason,reasoning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (result["run_id"], index, step.get("round_no"), step.get("kind"), step.get("tool"),
             json.dumps(step.get("args", {}), ensure_ascii=False),
             None if step.get("ok") is None else int(bool(step.get("ok"))), step.get("elapsed_ms"),
             step.get("result_preview"), json.dumps(step.get("result_data", {}), ensure_ascii=False),
             step.get("reason") or step.get("detail"), step.get("reasoning")),
        )
    conn.commit()
    conn.close()


def get_agent_run(run_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,provider,model,created_at FROM agent_runs WHERE run_id=?", (run_id,))
    run = cursor.fetchone()
    if not run:
        conn.close()
        return None
    cursor.execute("SELECT step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason,reasoning FROM agent_steps WHERE run_id=? ORDER BY step_index", (run_id,))
    steps = cursor.fetchall()
    conn.close()
    keys = ("step_index", "round_no", "kind", "tool", "args", "ok", "elapsed_ms", "result_preview", "result_data", "reason", "reasoning")
    parsed_steps = []
    for row in steps:
        item = dict(zip(keys, row))
        for key in ("args", "result_data"):
            try:
                item[key] = json.loads(item[key] or "{}")
            except (ValueError, TypeError):
                item[key] = {}
        parsed_steps.append(item)
    return {"run": dict(zip(("run_id", "chat_id", "question", "reply", "status", "rounds", "duration_ms", "total_tokens", "provider", "model", "created_at"), run)), "steps": parsed_steps}


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


def add_memory(content, category="偏好", source="user"):
    """source: 'user' 用户手动添加；'agent' 助手按用户要求写入。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO memories(content,category,source) VALUES(?,?,?)",
        (content[:1200], category[:40], source[:16]),
    )
    memory_id = cursor.lastrowid
    cursor.execute("DELETE FROM memories WHERE id NOT IN (SELECT id FROM memories ORDER BY id DESC LIMIT 100)")
    conn.commit()
    conn.close()
    return memory_id


def list_memories(limit=100):
    # 前四个字段的下标保持不变，source 追加在末尾，避免影响已有调用方
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,content,category,created_at,COALESCE(source,'user') FROM memories ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    conn.close()
    return rows


def delete_memory(memory_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def count_memories():
    """记忆总条数。用来在提示词里说清「只在上下文里放了最近 N 条，另有 M 条靠检索」。"""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    return int(total)


# 检索历史对话时扫的是 agent_runs 而不是 messages：
# messages 只有 role+content，一句问一句答各占一行、没有时间戳，检索出来是一堆
# 断句；agent_runs 天然是「问 + 答」成对的，还带 created_at，按时间说「你上次
# 在 11 月 3 日问过」才说得出口。
def list_runs_for_recall(limit=1000):
    """给检索用的问答对索引。reply 截断到 400 字：够判断相关性，不至于把整篇回答读进内存。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT run_id,chat_id,question,substr(COALESCE(reply,''),1,400),created_at "
        "FROM agent_runs ORDER BY rowid DESC LIMIT ?",
        (max(1, min(int(limit), 5000)),),
    ).fetchall()
    conn.close()
    return rows


def count_runs():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    conn.close()
    return int(total)


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


# ---------- 课程进度 ----------

def list_course_progress(course_id):
    """返回 {chapter_id: {...}}。没有记录的章节不在结果里，由上层补 todo。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chapter_id,status,mastery,notes,updated_at FROM course_progress WHERE course_id=?",
        (course_id,),
    ).fetchall()
    conn.close()
    return {
        row[0]: {"status": row[1] or "todo", "mastery": int(row[2] or 0),
                 "notes": row[3] or "", "updated_at": row[4]}
        for row in rows
    }


def set_course_progress(course_id, chapter_id, status=None, mastery=None, notes=None):
    """只更新传进来的字段。status 与 mastery 分开，是因为「看过」和「学会」是两件事。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO course_progress(course_id,chapter_id) VALUES(?,?)",
        (course_id, chapter_id),
    )
    sets, params = [], []
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if mastery is not None:
        sets.append("mastery=?")
        params.append(int(mastery))
    if notes is not None:
        sets.append("notes=?")
        params.append(notes)
    if sets:
        sets.append("updated_at=CURRENT_TIMESTAMP")
        params.extend([course_id, chapter_id])
        cursor.execute(
            "UPDATE course_progress SET %s WHERE course_id=? AND chapter_id=?" % ", ".join(sets),
            params,
        )
    conn.commit()
    row = cursor.execute(
        "SELECT status,mastery,notes,updated_at FROM course_progress WHERE course_id=? AND chapter_id=?",
        (course_id, chapter_id),
    ).fetchone()
    conn.close()
    return {"status": row[0], "mastery": int(row[1] or 0), "notes": row[2] or "", "updated_at": row[3]}


def add_course_question(course_id, chapter_id, question, answer=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO course_questions(course_id,chapter_id,question,answer) VALUES(?,?,?,?)",
        (course_id, chapter_id, (question or "")[:4000], (answer or "")[:20000]),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def list_course_questions(course_id, chapter_id=None, limit=30):
    conn = sqlite3.connect(DB_PATH)
    if chapter_id:
        rows = conn.execute(
            "SELECT id,chapter_id,question,answer,created_at FROM course_questions "
            "WHERE course_id=? AND chapter_id=? ORDER BY id DESC LIMIT ?",
            (course_id, chapter_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,chapter_id,question,answer,created_at FROM course_questions "
            "WHERE course_id=? ORDER BY id DESC LIMIT ?",
            (course_id, limit),
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "chapter_id": r[1], "question": r[2], "answer": r[3], "created_at": r[4]}
        for r in rows
    ]


# ======================
# 备份
# ======================
#
# 整个站的长期价值都在一个 chats.db 里：几个月的对话、课程进度、考过的问题、
# 手动记下的偏好。它没有副本——硬盘坏一次、手滑删一次就全没了，而这些数据
# 不像代码可以从 git 恢复。
#
# 所以每次启动时留一份快照。刻意做得「不精确」：
# - 一天只留一份，当天重启多次不再覆盖。当天第一次启动前的状态往往才是
#   最想要的那个；如果每次重启都覆盖，手滑删完数据再重启就把好备份冲掉了。
# - 只留最近 7 天。备份的价值在于「最近能回退」，不是做归档。


def _db_has_data(db_file: str) -> bool:
    """判断库里有没有真实内容。刚 init_db 出来的空壳不值得占一个备份位。"""
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT count(*) FROM chats").fetchone()[0] > 0
    except sqlite3.Error:
        # 表还没建，说明是全新的库
        return False
    finally:
        conn.close()


def _prune_backups(dest_dir: str, keep: int) -> None:
    """按文件名里的日期排序，删掉最旧的。名字带 ISO 日期，字母序就是时间序。"""
    names = sorted(
        name for name in os.listdir(dest_dir)
        if name.startswith("chats-") and name.endswith(".db")
    )
    if keep <= 0:
        stale = names
    else:
        stale = names[:-keep]
    for name in stale:
        try:
            os.remove(os.path.join(dest_dir, name))
        except OSError as exc:
            logger.warning("旧备份删不掉 %s：%s", name, exc)


def backup_db(dest_dir: str | None = None, keep: int = BACKUP_KEEP,
              day: str | None = None) -> str | None:
    """把当前库整份快照到备份目录，一天一份，只留最近 keep 天。

    返回备份文件路径；跳过（当天已有/空库/库不存在）或失败时返回 None。
    **任何异常都只记日志，绝不抛出**——备份不该成为服务启动的拦路石。
    """
    target_dir = dest_dir or BACKUP_DIR
    try:
        if not os.path.exists(DB_PATH) or not _db_has_data(DB_PATH):
            return None

        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "chats-%s.db" % (day or time.strftime("%Y-%m-%d")))
        if os.path.exists(target):
            return None

        # 用 sqlite 自带的备份接口，而不是直接复制文件：万一有别的进程
        # （命令行、另一个实例）正在写，复制可能拿到写了一半的页。
        src = sqlite3.connect(DB_PATH)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        _prune_backups(target_dir, keep)
        logger.info("数据库已备份到 %s", target)
        return target
    except Exception as exc:  # noqa: BLE001 - 备份失败不能拖垮启动
        logger.warning("数据库备份失败（不影响启动）：%s", exc)
        return None
