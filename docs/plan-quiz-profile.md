# 第一优先与第二优先的落地方案：题库判分 + 摸底画像

> 状态：设计稿，未写代码。范围只含「每日判分」与「摸底 + 画像」两项，
> 不含排期器（第三优先）与项目沙箱（第四优先）。
>
> **进度：第 ① 步（表与函数）已落地**（commit `2fba7e9`，tag `v0.17.0-quiz-db`）。
> 三张表、`course_progress` 两列、8 个存取函数、`test_quiz_db.py` 88 项已通过；
> 真实 `chats.db` 已迁移。
>
> **第 ② 步（判分规则）已落地**（`backend/quiz.py`，tag `v0.18.0-quiz-grade`）：
> 三种题型判分、题库校验、`public_items` 剥答案、`chapter_state` 合并作答、
> `grade_with_model` 兜底、`record()` 判分即落库。`test_quiz.py` 142 项通过。
>
> **第 ③ 步（诊断卷 + 前三章题库）已落地**（tag `v0.19.0-quiz-content`）：
> 第 1/7/19 章各 3 题（选择 + 填空 + 要点各一道），诊断卷 20 题覆盖 10 个阶段；
> `quiz.load_diagnostic` / `validate_diagnostic` / `score_diagnostic` 三个函数；
> `test_quiz_content.py` 132 项（含「每道题拿自己的答案必须判 100」的自检）。
> 顺带修了第 19 章正文两处余弦公式错误，见文末附录。
>
> **第 ④ 步（提交接口 + mastery 回写）已落地**（tag `v0.20.0-mastery`）：
> `POST /quiz/{id}/submit`；`quiz.mastery_target` / `apply_mastery` /
> `manual_done_mastery` 三个函数；`PATCH /course/progress` 现在会把手点的
> 掌握度压到「有证据的那个值」；`load_chapter` 顺带下发 `quiz`（已剥答案）。
> `test_quiz_mastery.py` 36 项 + `test_quiz_api.py` 31 项。
> **真实库里 course_progress / quiz_attempts 都是 0 行**，所以没有历史数据要迁移，
> 第一次真实写入会在用户下次点「学完」或答题时发生。
> 下一步是 ⑤ 前端答题界面。

## 0. 动手前必须先认的三件事

这三条是查过现存内容之后的事实，直接决定方案形状：

1. **现有自测题不能直接当判分题用。** `curriculum/agent-dev/chapters/*.md` 里
   共 99 个 `<details>` 折叠块：
   - 75 个题干写在 `<summary>1. ...</summary>` 里；
   - 12 个 `<summary>` 只写「参考答案」，题干在正文段落里；
   - 12 个是折叠代码或附加说明，不是题。
   能算自测题的约 87 道，**其中只有 7 章出现过 `A. B. C. D.` 选项**。
   结论：93% 是开放题，靠正则抽取得到的是「一段解释」，不是「可判分的答案」。

2. **所以题库要新写，不是从 md 里抽。** 原 `<details>` 保留，当作「读的时候本来
   就该看一眼」的练习，不计分。判分题另建一份，每章 3 道（33 × 3 = 99 道），
   题型配比 1 选择 + 1 填空 + 1 要点。

3. **`course_progress` 里 33 章的 mastery 目前全靠手点。** `learn.js` 的
   `toggleDone()` 直接把 `mastery` 写成 2。这意味着下游任何「按掌握度推荐」
   拿到的是用户自称的数据。判分不落到这张表上，第二项就没有可信输入。

---

## 1. 表

### 1.1 新增 `quiz_attempts`：一次作答一行

```sql
CREATE TABLE IF NOT EXISTS quiz_attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    item_id TEXT NOT NULL,            -- s1c3-q2，与题库文件里的 id 一致
    kind TEXT NOT NULL,               -- choice / blank / rubric
    answer TEXT NOT NULL,             -- 用户作答原文，不截断（要点判分要用）
    score INTEGER NOT NULL,           -- 0-100
    passed INTEGER NOT NULL,          -- 0/1，按作答当时的 PASS_SCORE 得出结论
    grader TEXT NOT NULL,             -- local / model
    detail TEXT DEFAULT '',           -- 命中与漏掉的要点，原样回给用户
    seconds INTEGER DEFAULT 0,
    flagged INTEGER DEFAULT 0,        -- 1 = 用户点了「这题判错了」
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_ch
    ON quiz_attempts(course_id, chapter_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_item
    ON quiz_attempts(course_id, item_id, created_at);
```

三个刻意的设计：

- **`passed` 单独存，不靠 `score >= 70` 现算。** 阈值以后一定会调（开放题判分
  调阈值是常态），历史结论必须留痕，否则「上周判过的题这周变成没过」，用户会
  以为系统在乱改。
- **`grader` 记 local / model。** 同一道题两种判法结果可能不一致，出了问题要能
  分清是规则还是模型判的。
- **`flagged` 不建新表。** 申诉的粒度就是「这一次作答」，一列够用。

### 1.2 新增 `profile_skills`：能力画像，10 行

```sql
CREATE TABLE IF NOT EXISTS profile_skills(
    course_id TEXT NOT NULL,
    skill TEXT NOT NULL,              -- s1..s10，对应 course.json 的 stage id
    level INTEGER DEFAULT 0,          -- 0 未测 / 1 听过 / 2 能写 / 3 能讲
    evidence TEXT DEFAULT '',         -- 「诊断卷 4/20、9/20」，可回溯
    source TEXT DEFAULT 'diagnostic', -- diagnostic / quiz / manual
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (course_id, skill)
);
```

**为什么是 stage 粒度而不是 33 章粒度**：定制路线的决策单位是「从哪个阶段
开始插进去」，不是「跳过哪一章」。章级精度已经由 `course_progress.mastery`
承担，再存一份就是两套真相。

### 1.3 新增 `diagnostics`：诊断卷的汇总

```sql
CREATE TABLE IF NOT EXISTS diagnostics(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    finished_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    payload TEXT DEFAULT '',          -- JSON：每题答案与分数，便于事后对拍判分规则
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

明细照样进 `quiz_attempts`，`item_id` 用 `diag-s3-q1` 前缀区分。这张表只放
「这一次诊断的结论」，用来算分级、也用来在画像页显示「你上次测是什么时候」。

### 1.4 列扩展：`course_progress` 加两列

```python
_ensure_column(cursor, "course_progress", "quiz_score", "INTEGER")
_ensure_column(cursor, "course_progress", "quiz_at", "TEXT")
```

- `quiz_score`：该章最近一轮判分得分，目录红绿灯直接读它，前端不用 join。
- `quiz_at`：最近作答时间，用来区分「从没测过」和「测过但很久了」。
- 用现成的 `_ensure_column`，不重建表，旧库无痛升级。

**不加 `passed` 列**：过没过由 `quiz_score >= PASS_SCORE` 现算。这一列变的是
「结论」，不是「事实」，存下来只会和阈值打架。

---

## 2. 题库文件格式

位置：`curriculum/agent-dev/quiz/<chapter_id>.json`，33 个文件。
放文件而不是库，理由是跟 `chapters/*.md` 同一套道理：能 git、能手改、能 diff。
总题量 99 道，全量加载不到 200 KB，不需要索引。

```json
{
  "chapter_id": "s1c3",
  "items": [
    {
      "id": "s1c3-q1",
      "kind": "choice",
      "stem": "1000 字中文喂给 gpt-4-turbo，最可能先撞哪个上限？",
      "options": ["A. context_length", "B. max_tokens", "C. rate limit", "D. 都不撞"],
      "answer": "A",
      "explain": "……",
      "objective": 0,
      "level": 1
    },
    {
      "id": "s1c3-q2",
      "kind": "blank",
      "stem": "控制单次回复最长多少 token 的参数是 ____。",
      "blanks": ["max_tokens"],
      "explain": "……",
      "objective": 1,
      "level": 1
    },
    {
      "id": "s1c3-q3",
      "kind": "rubric",
      "stem": "用一句话解释为什么同样一句话中文比英文贵。",
      "must": ["token 数量不同", "中文单字通常占一个 token", "计费按 token 而非字数"],
      "forbid": ["因为中文更复杂"],
      "explain": "……",
      "objective": 2,
      "level": 2
    }
  ]
}
```

两个字段值得单说：

- **`objective` 是下标**，指向 `course.json` 里该章 `objectives` 数组的第几条。
  「这题在考哪个学习目标」从此是结构化事实，不靠人记。写题库时如果某题对不上
  任何 objective，说明这题该删。
- **`forbid` 是反例要点**。开放题判分最容易出的错是「用户写了正确的废话，关键词
  全中但结论反了」。`forbid` 命中即判不过，这是唯一能挡住这种错的办法。

---

## 3. 改动清单

按文件排，编号即实施顺序参考（顺序另见第 4 节）。

### 后端

**① `backend/database.py`**

- `init_db()` 里新增三张表与四个索引（见第 1 节）。
- `_ensure_column` 加 `quiz_score` / `quiz_at` 两列。
- `set_course_progress()` 签名加 `quiz_score=None, quiz_at=None`。这个函数已经是
  「只更新传进来的字段」的写法，照现有风格加两个 `if` 分支即可，不要另写一个
  update 函数——两套写入逻辑迟早不一致。
- 新增函数（全部按现有风格：显式 `sqlite3.connect`、`conn.close()`、返回 dict）：
  - `add_quiz_attempt(course_id, chapter_id, item_id, kind, answer, score, passed, grader, detail, seconds) -> int`
  - `list_quiz_attempts(course_id, chapter_id=None, item_id=None, limit=50)`
  - `latest_quiz_scores(course_id) -> {chapter_id: score}`（一次查全，给 `decorate` 用，别在循环里查库）
  - `flag_quiz_attempt(attempt_id, flagged=1) -> bool`
  - `upsert_profile_skill(course_id, skill, level, evidence, source)`
  - `list_profile_skills(course_id) -> {skill: {...}}`
  - `save_diagnostic(course_id, total, correct, payload) -> int`
  - `latest_diagnostic(course_id) -> dict | None`

**② 新增 `backend/quiz.py`**（与 `course.py` 对称：只读写 + 判分，不生成内容）

- `load_quiz(course_id, chapter_id) -> dict`：读 json，校验 `item.id` 与章 id 前缀
  一致、`kind` 在枚举内、`objective` 不越界。校验失败直接 `QuizError`，不要静默
  跳过——题库写错却还能跑，是后面所有判分失真的源头。
- `public_items(quiz) -> list`：**剥掉 `answer` / `blanks` / `must` / `forbid` /
  `explain`**。给前端的永远是这一份。
- `grade(item, answer) -> dict`：
  - `choice`：取首字母比对，容忍「B」「b」「B. rate limit」三种写法。
  - `blank`：去空格、全半角标点、大小写归一后比对；多空题按命中比例给分。
  - `rubric`：`must` 命中率 ≥ 2/3 且 `forbid` 零命中 → pass；`score = 命中率 × 100`。
  - 返回 `{score, passed, detail, grader:"local"}`，`detail` 里写清「漏了：计费按
    token 而非字数」，不要只回一个分数。
- `grade_with_model(item, answer) -> dict`：只在 rubric 题本地判分落在
  0 < score < 阈值的模糊区间时调用，要求返回 JSON `{hit:[], miss:[], score, reason}`，
  并强制 `grader:"model"`。**不要每题都调模型**：99 道题全走模型，成本和结果波动
  都不可接受。
- `chapter_state(course_id, chapter_id) -> dict`：合并题库与历史作答，给前端
  `{total, passed, latest_score, last_at, items:[{id, passed, score}]}`。
- `PASS_SCORE = 70`。

**③ `backend/course.py`**

- `decorate()` 给每章贴 `quiz: {total, passed, latest_score}`，数据来自
  `latest_quiz_scores()` 一次查询。
- `load_chapter()` 的返回值拆两半：`markdown` **切掉 `## 自测` 到下一个 `##`** 的
  整段（服务端切，别在前端拿正则切 markdown，那是最容易切坏的地方），另给
  `quiz: public_items(...)`。这样一个请求拿全，前端不用二次跳转。
- `tutor_context()` 增加一段事实：`"他在这章的判分情况：第 2 题错两次，考点是
  temperature 被当成控制长度的参数。"` 这比现在「他问过什么」硬得多，导师据此
  说话才不空。
- **`_is_passed()` 不加 quiz 条件。** 这条必须守：现在 33 章里凡是手点过「学完」
  的都会因为「没答过题」被判成没过关，进度条当场回退，用户会以为数据丢了。
- 新增 `skip_allowed(chapters, profile)`：返回画像允许跳过的 chapter id 集合
  （stage level ≥ 2 的章全进），留给第二项用。

**④ 新增 `backend/profile.py`**

- `evaluate_diagnostic(answers, diagnostic) -> {stage: level}`：**直接用 `quiz.score_diagnostic()`**，
  不要另写一套分级。原稿写的「<40% → 0，40–60 → 1，60–85 → 2，>85 → 3」在 2 题的
  粒度上落不进去（百分比只有 0/50/100），实际规则是：
  **每阶段 2 题，答对几题就是几级，封顶 2 级**；level 3 只能由章节 rubric 题拿到，
  因为选择题和填空题只能证明「认得」，证明不了「能讲」。
  也刻意没给题目设权重——试过「进阶题 2 分」，结果基础题错了、进阶题蒙对合计 2 分
  照样判二级，整个阶段被跳过；分级直接决定能不能跳，宁可保守。
- `apply_to_progress(course, levels) -> 变更清单`：level ≥ 2 的 stage 内所有章写
  `status=done, mastery=2`，**并在 `notes` 里写死「诊断判定，非实际学习」**。
  不做这一步，半年后自己分不清哪些章是真学过的。
- `build_skip_set(course, levels) -> set`。
- 只写「建议」，不直接改 `recommend_next` 的内部逻辑。

**⑤ `backend/main.py`**

- `GET /course/chapter/{id}` 返回值加 `quiz`（已在 ③ 里做，这里只透传）。
- `POST /quiz/{chapter_id}/submit`：body `{item_id, answer, seconds}` → 调
  `quiz.grade()`，必要时 `grade_with_model()` → 写 `quiz_attempts` → 回写
  `course_progress.quiz_score/quiz_at` → 返回 `{score, passed, detail, explain, chapter, next}`。
  `explain` 只在校验之后返回，**不能在 GET 里给**。
- `POST /quiz/{chapter_id}/flag`：body `{attempt_id}`。
- `GET /quiz/{chapter_id}/history`：最近 20 次作答。
- `mastery` 回写规则（写在一个函数里，别散在接口里）：
  - 本章 3 题全过 → `mastery = max(现mastery, 2)`
  - 有任一道没过 → `mastery = min(现mastery, 1)`
  - **只在有作答记录时执行**，手点「学完」仍按老逻辑写 2，但答错一轮会被降下来。
    这是唯一能让 mastery 从「自称」变成「有据」的路径。
- `POST /diagnostic/start`、`POST /diagnostic/submit`、`GET /profile`。
- `PATCH /course/progress` **保持不动**。

### 前端

**⑥ `frontend/learn.js`**

- `renderChapter()`：`payload.markdown` 已不含自测段，在正文后插入
  `renderQuiz(payload.quiz)`。
- 新增 `renderQuiz(state)`：三种控件（单选 / 填空 / 要点输入框），提交后原地显示
  判定 + `explain` + 「漏了哪几点」；每题一个「这题判错了」小按钮 → flag 接口。
- `renderToc()`：`dot` 之外加一个 6px 方块，`quiz.latest_score < 70` 显示为未过。
  目录是唯一能一眼看出「哪些章需要回头」的地方。
- `toggleDone()`：`mastery: 2` 改成「有作答记录才给 2，否则给 1」。**这一行不改，
  第一项就白做了**——用户手点三次就把 33 章刷满 mastery 2。
- 新增诊断入口：首次进入且 `profile_skills` 为空时，在 `reader` 里显示一张卡
  「先花 10 分钟测一下，不用从第 1 章开始」，答完直接跳到推荐章。

**⑦ `frontend/learn.css`**：加 `.quiz-*` 与 `.quiz-verdict`，沿用现有变量，不新增色板。

### 内容与脚本

**⑧ 新增 `scripts/build_quiz.py`**

- 读 33 章 md，把 `<details>` 抽成**草稿骨架**（`kind` 统一填 `rubric`，`must`
  留空数组），输出到 `curriculum/agent-dev/quiz/_draft/<chapter>.json`。
- **不要自动猜题型**：全库只有 7 章有选项，靠正则判定 choice 会大面积判错。
- 草稿由人（或模型 + 人工过一遍）补 `must` / `forbid` / `explain`，确认后移到
  `quiz/<chapter>.json`。

**⑨ 题库撰写**：33 章 × 3 题。优先级：先写第 1、7、19 章（正好是他一直没抽读的
那三章），跑通判分再补剩余 30 章。**不要一次写满 99 道**，判分规则一定会因为前
三章暴露的问题而改。

**⑩ `curriculum/agent-dev/quiz/_diagnostic.json`**：10 stage × 2 题 = 20 题，
**全部 choice / blank**，必须能确定性判分。12 分钟以内能答完。

实际文件结构（比原稿多了 `stage`，少了 `objective`）：

```json
{
  "id": "diagnostic-v1", "minutes": 12, "max_level": 2,
  "items": [
    {"id": "diag-s1-q1", "stage": "s1", "kind": "choice",
     "stem": "...", "options": ["A. ...", "B. ..."], "answer": "B", "explain": "..."},
    {"id": "diag-s1-q2", "stage": "s1", "kind": "blank",
     "stem": "...", "blanks": [["采样", "抽样"]], "explain": "..."}
  ]
}
```

- `stage` 用 `course.json` 里的 stage id（`s1`…`s10`）。写成别的东西不会报错，
  但分数会落到一个永远读不到的画像行上——所以 `validate_diagnostic(stages=...)`
  会校验它。
- 题号规则 `diag-<stage>-qN`，与章节题库的 `<chapter>-qN` 同一套思路。
- 诊断卷**不给前端发 `stage`**：`public_items()` 只保留 id/kind/stem/options，
  阶段标签留着等于提前告诉用户「这题属于第 7 阶段」，会影响他的自我判断。

### 写题库的四条硬规则（都是踩过才知道的）

1. **`must` 的要点写成词组，不写整句。** `{"any": ["只能看到上文", "顺着上文"]}` 能命中，
   `"模型每一步只能看到上文"` 几乎永不命中。写整句不是规则太严，是题写错了。
2. **两个字的填空答案，必须再配一条「用户可能怎么写的长说法」。** 判分里只有
   ≥4 个字符的答案才启用包含匹配，所以 `"长度"` 只认精确的两个字；用户写
   「不看向量的长度」就会判错。配一个 `"向量长度"` 当长选项即可解决。
3. **`forbid` 必须带主语。** 写 `"模型自己发请求"` 而不是 `"发请求"`，
   否则正确回答里那句「你的代码去发请求」也会命中。
4. **每题都要挂 `objective`，且三个目标尽量全覆盖。** 三道题挤在同一个目标上，
   另外两个目标永远测不到，而且没人会发现——所以有测试盯着。

### 测试

**⑪ 新增 `backend/test_quiz.py`**：题库加载与校验、三种题型判分、阈值边界、
`public_items` 不含答案、33 章每章 ≥3 题、每个 `objective` 索引不越界、
`forbid` 命中即不过。

**⑫ 新增 `backend/test_profile.py`**：分级边界（39/40/59/60/84/85）、
诊断不覆盖已有真实学习记录的 notes、**诊断 level 3 后 `recommend_next` 不再返回
第 1 章**（这是第二项唯一的硬验收）。

**⑬ `backend/test_course.py` 增补**：`decorate` 带 `quiz` 字段后，原断言仍全过；
`_is_passed` 在无答题记录时行为不变（防回退）。

---

## 4. 实施顺序

| 步 | 内容 | 依赖 | 为什么在这 |
|---|---|---|---|
| 1 | ① 表与函数 | 无 | 纯新增，可回退 |
| 2 | ② `quiz.py` 判分 | 1 | 规则定下来才知道题库字段够不够 |
| 3 | ⑩ 诊断卷 + ⑨ 前三章题库 | 2 | 先小样本暴露判分缺陷 |
| 4 | ⑤ 提交接口 + mastery 回写 | 2、3 | 第一次真正改到已有数据 |
| 5 | ⑥⑦ 前端答题与红绿灯 | 4 | |
| 6 | ⑪ 测试 | 2–5 | |
| 7 | ④ `profile.py` + ⑤ 诊断接口 | 1、3 | 复用判分，不重复写 |
| 8 | ③ `recommend_next` 接 `skip_allowed` | 7 | 改已有算法，紧挨着画像做 |
| 9 | ⑫ 测试 | 7、8 | |
| 10 | ⑧ 剩余 30 章题库 | 6 | 判分规则稳定后再批量写 |

②③ 和 ⑧ 可以并行；其余按表。

---

## 5. 四个必须现在拍板的取舍

1. **旧 87 道自测题不改造，另写 99 道判分题。** 代价是内容工作量翻倍，收益是
   判分规则能立住。反过来做（硬抽旧题当判分题），阈值一定调不准，用户会觉得
   「明明答对了判我错」——这种体验一次就够毁掉整个判分功能。

2. **`_is_passed()` 绝不加 quiz 条件。** 只能通过 `mastery` 间接影响推荐。
   直接加等于把历史进度清零。

3. **开放题判分默认本地跑，模型只兜模糊区间。** 全模型判分不可复现、不可对拍，
   也让他每答一题都在等网络。

4. **诊断结果必须标来源。** `notes` 里写清「诊断判定，非实际学习」。这条不是
   洁癖：第三项的排期器要按「真实掌握度」算周任务量，混进诊断结果就会把 22 小时
   的课算成 6 小时。

---

## 6. 验收标准

- 33 章每章都有 ≥3 道题，每题都指向一个合法 `objective`。
- `GET /course/chapter/{id}` 的响应里搜不到 `answer` 字样。
- 同一道 rubric 题同样答案连判 3 次，结果完全一致（本地规则必须可复现）。
- 手点「学完」得到 `mastery=1`；答对 3 题后升到 2；之后再答错 1 题降到 1。
- 新旧库都能启动（旧库只有 `course_progress`，启动后自动补两列）。
- `test_course.py` / `test_quiz.py` / `test_profile.py` 全绿，`_is_passed` 行为未变。

---

## 7. 附录：第 ③ 步实际落地时发现并改掉的三件事

### 7.1 第 19 章正文的余弦公式是错的（内容 bug，已修）

`s6c2.md` 两处：

- 代码 `sim_scores = [1 - np.dot(...)/(norm*norm) ...]` 算出来的是**距离**，
  不是相似度。按原样跑，「香蕉富含钾元素」会排第一——注释里写的期望输出
  （两个高一个低）根本不可能出现。改为直接算 `np.dot(...)/(norm*norm)`。
- 自测第 2 题问「`1 - distance.cosine(a,b)` 得 0.85，余弦值是多少」，原答案
  写 0.15。`distance.cosine` 本身就是 `1 - 余弦值`，减两次等于没减，正确答案是
  **0.85**。已连同解释一起改掉，并补了一句「距离越小越像，相似度越大越像，
  两者加起来等于 1」。

这条不是排版问题：题是照着正文写的，正文错了题目必然跟着错，而且会因为
「用户答对判错」把判分功能的信任度直接打掉。这也是「先抽读三章」的价值——
不读正文就写题，等于把错误固化进题库。

### 7.2 `forbid` 必须能认出否定（规则 bug，已修）

三条 rubric 题用自然语言试答时暴露的：像

> 「**不是模型自己发请求**，模型只输出 json；你的代码去执行，再把结果喂回对话。」

这样完全正确的回答，会被 `forbid: ["模型自己发请求"]` 判成「结论反了」，得 0 分。
开放题里「不是A，而是B」是最常见的正确句式，不处理它等于专门误伤答对的人。

修法：`_forbid_hit()` 逐次检查反例出现的位置，若紧邻的前 6 个字里有否定词
（不是/并不是/而不是/不能/不该/并非…），或紧邻的后 6 个字里有
「是错的/不对/不可能…」，这一次出现就不算命中；同一句里只要有一次是肯定地说出来，
仍然判 0。不能全文搜否定词——「模型自己发请求是对的，后面不是……」这种前后各说
一半的句子会被整体误判成否定。

`test_quiz.py` 补了四项：否定在前、否定在后、肯定命中、前后混杂。

### 7.3 诊断分级从「百分比分档」改成「答对题数」

2 题的粒度撑不起 40/60/85 三档，而且试过按权重（进阶题 2 分）会出现
「基础题错、进阶题蒙对 = 2 分 = 二级 → 整段跳过」的荒唐结论。
最终规则：**答对几题就几级，封顶 2 级**，level 3 只留给章节 rubric 题。
`quiz.score_diagnostic()` 的 docstring 里写了这两条理由。

### 7.4 mastery 回写的三条实际规则（与 8.1 节原稿的差别）

原稿写的是「三题全过 → `max(现, 2)`；有任一道没过 → `min(现, 1)`」。
实现时补了两种情况，否则会出现「答对一题就升级」或者「点一下就被降级」：

| 判分状态 | 目标 | 理由 |
|---|---|---|
| 一题都没答 | 不改 | 没证据不动，也不因此建一行空进度 |
| 答过的里面有过不去的 | 降到 1 | 唯一能把手点的 2 打回原形的路径 |
| 三题全答完且全过 | 升到 2 | 判分能给到的最高级 |
| 只答了一部分，但都对 | 不改 | 答对一题证明不了「能自己写出来」，也不该因此降级 |

三个配套决定：

1. **`status` 一律不动。** 掌握度是「会到什么程度」，status 是「学没学过」。
   混在一起的话，答错一题会让进度条倒退，用户以为数据丢了。
2. **手点「学完」的掌握度由服务端算，不接受前端自报。** `PATCH /course/progress`
   在 `status=done` 时会把传来的 mastery 压到 `manual_done_mastery()` 的结果
   （没全过 = 1，全过 = 2）。前端 `learn.js` 里那行写死的 `mastery: 2` 已删掉。
   不这么做，点三次就能把 33 章刷满，判分永远是空转。
3. **人评的 3 级不会被判分压低。** 升级用 `max`，所以已经是 3 的章全过仍是 3；
   只有「有题没过」才会降到 1。

留一个待定的问题：三题全过但没点「学完」时，`status` 仍是 `todo`，
于是推荐还是会推这一章。要不要在首次答题时把 status 置成 `learning`（推荐规则 1
会优先收尾这一章），等前端做完再定——那是个会改变推荐行为的决定，不适合顺手改。
