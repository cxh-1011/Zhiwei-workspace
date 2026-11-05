// =============================
// AI机器人前端 JS
// 阿里云部署版（前端健壮性加固版 v2）
// =============================


// 当前聊天ID
let currentChatId = null;
let chatCreation = null;
let knownFiles = new Set();
let paperReady = false;
// 是否允许助手写入学习档案（长期记忆 / 勾选任务）。默认关闭。
let allowWrites = false;
// 是否允许助手只读访问工作区代码。默认关闭。
let allowWorkspace = false;
// 是否允许助手修改工作区代码（write_file / edit_file）。默认关闭。
// 开启后后端会自动带上只读工具，每次写入都会先备份，改完可以一键撤销。
let allowCodeEdit = false;
// 是否允许助手在工作区内执行命令（run_command）。默认关闭。
// 与「允许改代码」分开：跑命令更危险。需要确认的命令会弹卡片等你点。
let allowCommand = false;
// 是否向模型请求思考过程（仅部分模型支持）。开启后回答上方会多出「思考」段落。
let showThinking = false;

// 首次加载聊天列表时，是否已经自动打开过最近一个会话
let initialLoadDone = false;



// =============================
// 一、接口地址：自动适配 /api 前缀
// =============================
// 说明：
// 1. 阿里云上一般用 nginx 把 /api/ 反向代理到后端（并去掉 /api/）；
// 2. 但本项目的后端 FastAPI 路由是根路径（/upload_paper、/paper_chat ...）。
// 所以这里默认先请求 "/api"，如果返回 404（说明没有做 /api 反代），
// 就自动回退到根路径再请求一次。两种部署方式都能跑通。
//
// 也支持用网址参数临时指定后端地址（本地调试很好用）：
//   index.html?api=http://127.0.0.1:8000
//   index.html?api=/api
//   index.html?api=
const API_QUERY = new URLSearchParams(location.search).get("api");

// 直连后端的兜底地址。
// 适用场景：网页是用 file:// 打开的，或者被某个"只做静态托管"的
// 预览服务器打开的 —— 这种情况下同源的 /api 请求必然失败，
// 必须直接请求后端绝对地址才能用。后端已开启 CORS，允许跨域。
const BACKEND_FALLBACKS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000"
];

function buildApiCandidates() {

    // 网址上显式指定了后端地址，就只用它
    if (API_QUERY !== null) {

        return [API_QUERY];

    }

    const list = ["/api", ""];

    for (const origin of BACKEND_FALLBACKS) {

        // 网页本来就是从这个地址打开的，同源写法已经覆盖，无需重复
        if (location.origin !== origin) {

            list.push(origin + "/api", origin);

        }

    }

    // 去重
    return [...new Set(list)];

}

const API_CANDIDATES = buildApiCandidates();

// 探测成功后缓存，后续请求不再重复试两次
let apiBase = null;



// =============================
// 二、请求封装
// =============================

// 统一的网络层错误说明
function describeNetworkError(err) {

    const name = (err && err.name) ? err.name : "Error";
    const msg = (err && err.message) ? err.message : String(err);

    const tips = [];

    if (location.protocol === "file:") {

        tips.push(
            "当前网页是用文件方式（file://）打开的。已尝试直连后端 " +
            "http://127.0.0.1:8000 但仍失败 —— 说明后端没有在运行。" +
            "请先双击项目根目录的 start.bat 启动后端。"
        );

    } else {

        tips.push(
            "请求没有到达服务器（后端未启动 / 未配置接口转发 / " +
            "连接被重置 / 被浏览器插件拦截，都会是这样）。"
        );

    }

    return "网络错误 " + name + "：" + msg + "  " + tips.join(" ");

}



// 按 HTTP 状态码给出人话解释
function httpErrorTips(status) {

    if (status === 502 || status === 503 || status === 504) {

        return "接口已经转发到后端，但后端没有响应 —— " +
            "多半是 Python 后端服务没启动或启动时就崩了（依赖没装齐是最常见原因）。";

    }

    if (status === 413) {

        return "文件超过了服务器允许的上传大小" +
            "（nginx 的 client_max_body_size），请让运维调大限制或换小文件。";

    }

    if (status === 500) {

        return "后端在处理时抛异常了，去看后端日志。" +
            "论文接口常见原因：pymupdf / langchain 等依赖缺失，或 DashScope 调用超时。";

    }

    if (status === 404) {

        return "这个接口地址不存在 —— 服务器没有把该地址转发给后端。";

    }

    if (status === 405) {

        return "请求方法不被允许（接口地址存在，但方法不对）。";

    }

    return "";

}



async function apiFetch(path, options) {

    const candidates =
        (apiBase === null)
            ? API_CANDIDATES
            : [apiBase];

    const attempts = [];

    let lastError = null;

    for (let i = 0; i < candidates.length; i++) {

        const base = candidates[i];

        const full = base + path;

        const isLast = (i === candidates.length - 1);

        try {

            const res = await fetch(full, options);

            // 前缀不对时后端一般返回 404，换个前缀再试
            if (res.status === 404 && !isLast) {

                attempts.push(
                    "✗ " + full + " → HTTP 404（该地址不存在，换下一种前缀重试）"
                );

                lastError = new Error("接口不存在：" + full);

                continue;

            }

            apiBase = base;

            attempts.push("✓ " + full + " → HTTP " + res.status);

            // 把尝试记录挂到 response 上，报错时可以展示出来
            res._attempts = attempts;

            return res;

        } catch (err) {

            // 用户主动停止（AbortController）不是网络故障，不要重试别的地址
            if (err && err.name === "AbortError") {

                throw err;

            }

            attempts.push(
                "✗ " + full + " → 网络错误 " +
                ((err && err.name) || "") + " " + ((err && err.message) || "")
            );

            lastError = err;

            if (isLast) {

                break;

            }

        }

    }

    const e =
        (lastError && lastError.name === "TypeError")
            ? new Error(describeNetworkError(lastError))
            : (lastError || new Error("网络请求失败"));

    e.attempts = attempts;

    e.isNetwork = true;

    throw e;

}



// 安全解析 JSON
// 后端报错时可能返回 HTML，直接 res.json() 会抛异常
async function parseJson(res) {

    const text = await res.text();

    if (!text) return {};

    try {

        return JSON.parse(text);

    } catch (e) {

        return { _raw: text };

    }

}



// 把 HTTP 错误翻译成人话
function httpErrorText(res, data) {

    let detail = "";

    if (data && typeof data === "object") {

        detail =
            data.detail ||
            data.message ||
            data.error ||
            data._raw ||
            "";

    }

    if (detail && typeof detail === "object") {

        detail = JSON.stringify(detail);

    }

    detail = String(detail || "")
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim();

    if (detail.length > 200) {

        detail = detail.slice(0, 200) + "…";

    }

    return "HTTP " + res.status +
        (res.statusText ? " " + res.statusText : "") +
        (detail ? "：" + detail : "");

}



// 一步到位：请求 + 解析 + 非 2xx 就抛带上下文的错误
async function apiJson(path, options) {

    const res = await apiFetch(path, options);

    const data = await parseJson(res);

    if (!res.ok) {

        const err = new Error(httpErrorText(res, data));

        err.status = res.status;

        err.attempts = res._attempts || [];

        err.tips = httpErrorTips(res.status);

        throw err;

    }

    return data;

}



// 把错误渲染成可读的 HTML
function errorToHtml(err) {

    let html = escapeHtml((err && err.message) ? err.message : String(err));

    if (err && err.tips) {

        html += "<br>" + escapeHtml(err.tips);

    }

    if (err && err.attempts && err.attempts.length) {

        html += '<div class="err-attempts">尝试记录：' +
            err.attempts.map(a => escapeHtml(a)).join("<br>") + "</div>";

    }

    return html;

}



// =============================
// 三、通用小工具
// =============================

function $(id) {

    return document.getElementById(id);

}


function escapeHtml(s) {

    return String(s === null || s === undefined ? "" : s)

        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");

}



// 状态提示框（论文助手用）
function setStatus(id, html, type) {

    const box = $(id);

    if (!box) return;

    box.innerHTML = html;

    box.className = "status-box" + (type ? " " + type : "");

}



// 按钮 loading 状态，防止重复点击
function setBtnLoading(btnId, loading, loadingText) {

    const btn = $(btnId);

    if (!btn) return;

    if (loading) {

        if (!btn.dataset.originalText) {

            btn.dataset.originalText = btn.innerHTML;

        }

        btn.disabled = true;

        btn.classList.add("loading");

        if (loadingText) {

            btn.innerHTML = loadingText;

        }

    } else {

        btn.disabled = false;

        btn.classList.remove("loading");

        if (btn.dataset.originalText) {

            btn.innerHTML = btn.dataset.originalText;

        }

    }

}



// 显示已选文件名
function showSelectedFile(inputId, hintId) {

    const input = $(inputId);

    const hint = $(hintId);

    if (!hint) return;

    const file =
        (input && input.files)
            ? input.files[0]
            : null;

    if (!file) {

        hint.textContent = "未选择文件";

        hint.classList.remove("active");

        return;

    }

    const size = (file.size / 1024 / 1024).toFixed(2);

    hint.textContent = "已选择：" + file.name + "（" + size + " MB）";

    hint.classList.add("active");

    if (inputId === "fileInput") {
        if ($("fileName")) $("fileName").value = file.name;
        if ($("contextName")) $("contextName").textContent = file.name;
        if (activeMode === "file") uploadFile();
    } else if (inputId === "paperInput") {
        paperReady = false;
        if ($("contextName")) $("contextName").textContent = file.name;
        if ($("uploadPaperBtn")) $("uploadPaperBtn").hidden = false;
    }

}



// 顶部连接告警告示条
function showBanner(msg, type) {

    const el = $("connBanner");

    if (!el) return;

    if (!msg) {

        el.style.display = "none";

        el.innerHTML = "";

        return;

    }

    el.className = "conn-banner" + (type ? " " + type : "");

    el.innerHTML = msg;

    el.style.display = "block";

}



// =============================
// 四、Markdown 渲染（CDN 挂了也能用）
// =============================

function renderMarkdown(text) {

    const src =
        (text === null || text === undefined)
            ? ""
            : String(text);

    if (typeof marked !== "undefined" && typeof marked.parse === "function") {

        try {

            return sanitizeMarkdownHtml(marked.parse(src));

        } catch (e) {

            // 落到下面的简易渲染

        }

    }

    return sanitizeMarkdownHtml(simpleMarkdown(src));

}

// Markdown 来自模型或用户输入，保留常见排版标签并移除脚本与危险属性。
function sanitizeMarkdownHtml(html) {
    const parsed = new DOMParser().parseFromString(String(html), "text/html");
    const allowed = new Set(["P", "BR", "STRONG", "EM", "DEL", "CODE", "PRE", "BLOCKQUOTE", "UL", "OL", "LI", "H1", "H2", "H3", "H4", "HR", "A", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD"]);
    parsed.body.querySelectorAll("*").forEach(node => {
        if (!allowed.has(node.tagName)) { node.replaceWith(...node.childNodes); return; }
        [...node.attributes].forEach(attr => {
            if (node.tagName !== "A" || !["href", "title"].includes(attr.name.toLowerCase())) node.removeAttribute(attr.name);
        });
        if (node.tagName === "A") {
            const href = node.getAttribute("href") || "";
            if (!/^(https?:|mailto:)/i.test(href)) node.removeAttribute("href");
            else { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
        }
    });
    return parsed.body.innerHTML;
}



// 极简 Markdown 兜底渲染（marked 加载失败时使用）
function simpleMarkdown(src) {

    let html = escapeHtml(src);

    // 代码块
    html = html.replace(
        /```([\s\S]*?)```/g,
        function (m, code) {

            return "<pre>" + code.replace(/^\n/, "") + "</pre>";

        }
    );

    // 行内代码
    html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");

    // 粗体
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");

    // 标题
    html = html.replace(/^###\s?(.*)$/gm, "<h4>$1</h4>");
    html = html.replace(/^##\s?(.*)$/gm, "<h3>$1</h3>");
    html = html.replace(/^#\s?(.*)$/gm, "<h3>$1</h3>");

    // 换行
    html = html.replace(/\n/g, "<br>");

    return html;

}



// =============================
// 五、加载聊天列表
// =============================

function loadChats() {

    const box = $("chatList");

    if (!box) return;

    apiJson("/chats")

        .then(data => {

            const list = (data && data.data) ? data.data : [];

            box.innerHTML = "";

            if (!list.length) {

                box.innerHTML =
                    '<div class="empty-hint">暂无聊天记录</div>';

                return;

            }

            list.forEach(chat => {

                const div = document.createElement("div");

                div.className = "chat-item";
                if (Number(chat[0]) === Number(currentChatId)) div.classList.add("active");

                div.innerHTML = `

                    <span onclick="openChat(${chat[0]})" title="${escapeHtml(chat[1])}">

                        ${escapeHtml(chat[1])}

                    </span>

                    <button onclick="deleteChat(${chat[0]})">

                        删除

                    </button>

                `;

                box.appendChild(div);

            });

            initialLoadDone = true;

        })

        .catch(err => {

            box.innerHTML =
                '<div class="empty-hint">聊天列表加载失败：' +
                escapeHtml((err && err.message) || String(err)) + '</div>';

        });

}



// =============================
// 六、新建聊天
// =============================

function newChat() {

    apiJson(
        "/create_chat?title=" + encodeURIComponent("新聊天"),
        { method: "POST" }
    )

        .then(data => {

            if (data && data.id !== undefined) {

                currentChatId = data.id;

            }

            initialLoadDone = true;

            clearChat();

            loadChats();

        })

        .catch(err => {

            alert("新建聊天失败：" + err.message);

        });

}



// =============================
// 七、删除聊天
// =============================

function deleteChat(id) {

    apiJson("/delete_chat/" + id, { method: "DELETE" })

        .then(() => {

            // 删掉的正好是当前打开的会话，就清空右侧并置空，下次发言会自动新建一个
            if (id === currentChatId) {

                currentChatId = null;

                clearChat();

            }

            loadChats();

        })

        .catch(err => {

            alert("删除失败：" + err.message);

        });

}



// 没有当前会话时（例如刚把会话删了），先自动建一个再聊
async function ensureChatId() {

    if (currentChatId) return currentChatId;

    if (chatCreation) return chatCreation;

    chatCreation = apiJson(

        "/create_chat?title=" + encodeURIComponent("新聊天"),

        { method: "POST" }

    ).then(data => {

        if (data && data.id !== undefined) currentChatId = data.id;
        initialLoadDone = true;
        loadChats();
        return currentChatId;
    }).finally(() => { chatCreation = null; });

    return chatCreation;

}



// =============================
// 八、打开聊天
// =============================

function openChat(id) {

    currentChatId = id;
    selectMode("chat");
    toggleHistory(false);

    document.querySelectorAll(".chat-item").forEach(item => {
        item.classList.toggle("active", item.querySelector("span")?.getAttribute("onclick") === `openChat(${id})`);
    });

    $("sidebar")?.classList.remove("open");
    $("mobileScrim")?.classList.remove("open");

    apiJson("/messages/" + id)

        .then(async data => {

            const box = $("chat");

            if (!box) return;

            box.innerHTML = "";

            const titleEl = $("currentTitle");
            const activeItem = [...document.querySelectorAll(".chat-item")].find(item => item.querySelector("span")?.getAttribute("onclick") === `openChat(${id})`);
            if (titleEl) titleEl.textContent = activeItem?.querySelector("span")?.textContent.trim() || "对话";

            const list = (data && data.data) ? data.data : [];
            const traceData = await apiJson("/agent_runs?chat_id=" + encodeURIComponent(id)).catch(() => ({ items: [] }));
            const traces = [...(traceData.items || [])];

            if (!list.length) {
                addMessage("ai", MODE_INFO.chat.greeting);
                if (titleEl) titleEl.textContent = activeItem?.querySelector("span")?.textContent.trim() || "新对话";
                return;
            }

            list.forEach(msg => {
                if (msg[2] === "ai") {
                    const traceIndex = traces.findIndex(item => item.reply === msg[3]);
                    if (traceIndex >= 0) {
                        const run = traces.splice(traceIndex, 1)[0];
                        const bubble = document.createElement("div");
                        bubble.className = "ai";
                        if (Array.isArray(run.steps) && run.steps.length) {
                            bubble.appendChild(renderAgentTrace(run.steps, run.status, run.rounds, run.duration_ms, undefined, run.run_id));
                        }
                        const answer = document.createElement("div");
                        answer.className = "agent-answer";
                        answer.innerHTML = renderMarkdown(msg[3]);
                        bubble.appendChild(answer);
                        // 回看历史时来源埋在 steps 里，捞出来照原样再挂一遍
                        const past = collectSources(null, { steps: run.steps });
                        if (past.length) bubble.appendChild(renderSources(past));
                        box.appendChild(bubble);
                        return;
                    }
                }
                addMessage(msg[2], msg[3]);
            });

        })

        .catch(err => {

            addMessage("ai", "历史消息加载失败：" + err.message);

        });

}



// =============================
// 九、发送消息
// =============================

// 工具名称 → 中文说明，流式状态行和过程时间线共用
const TOOL_LABELS = {
    web_search: "联网搜索",
    paper_search: "检索论文",
    scholarly_search: "学术论文检索",
    search_uploaded_files: "检索上传资料",
    list_uploaded_files: "查看资料列表",
    remember: "记住这条信息",
    list_learning_tasks: "读取任务列表",
    set_task_status: "更新任务状态",
    list_dir: "查看目录",
    read_file: "读取文件",
    grep_code: "搜索代码",
    repo_map: "查看仓库结构",
    git_status: "查看改动状态",
    git_diff: "查看代码差异",
    git_log: "查看提交历史",
    write_file: "写入文件",
    edit_file: "修改文件",
    run_command: "运行命令",
    update_todos: "更新任务清单"
};

function toolLabel(name) {
    return TOOL_LABELS[name] || name || "工具";
}

// 工具参数 → 中文名。目的不是逐个翻译，而是让「他到底查了什么」一眼能读懂。
const ARG_LABELS = {
    query: "检索内容",
    question: "问题",
    keyword: "关键词",
    pattern: "匹配模式",
    filename: "文件",
    file: "文件",
    path: "路径",
    dir: "目录",
    k: "返回条数",
    top_k: "返回条数",
    category: "分类",
    content: "内容",
    task_id: "任务编号",
    status: "状态",
    url: "地址",
    doi: "DOI",
    title: "标题",
    staged: "看暂存区",
    stat_only: "只看统计",
    start_line: "起始行",
    max_lines: "读取行数",
    max_chars: "输出上限",
    limit: "条数",
    old_string: "原文锚点",
    new_string: "替换为",
    replace_all: "替换全部",
    command: "命令",
    cwd: "目录",
    timeout: "超时",
    items: "清单"
};

function formatArgValue(value) {
    if (value === null || value === undefined) return "";
    // 清单这类对象数组如实序列化会把一行撑满，只报条数
    if (Array.isArray(value)) {
        if (!value.length) return "";
        if (value.every(item => item && typeof item === "object")) return value.length + " 条";
        return value.slice(0, 5).join("、");
    }
    let text = typeof value === "object" ? JSON.stringify(value) : String(value);
    text = text.replace(/\s+/g, " ").trim();
    return text.length > 90 ? text.slice(0, 90) + "…" : text;
}

// 把参数对象压成一行可读文字，最多三条，避免长参数把面板撑爆
function describeArgs(args) {
    if (!args || typeof args !== "object") return "";
    const parts = [];
    Object.keys(args).forEach(key => {
        const value = formatArgValue(args[key]);
        if (!value) return;
        parts.push((ARG_LABELS[key] || key) + "：" + value);
    });
    if (!parts.length) return "";
    return parts.slice(0, 3).join(" · ");
}

// 流式过程中的「过程面板」。边跑边往里加条目，跑完由 renderAgentTrace 接管。
function createLiveTrace() {
    const root = document.createElement("details");
    root.className = "agent-trace live";
    root.open = true;

    const summary = document.createElement("summary");

    const spin = document.createElement("span");
    spin.className = "trace-spin";

    const label = document.createElement("span");
    label.textContent = "正在思考…";

    summary.append(spin, label);

    const body = document.createElement("div");
    body.className = "trace-live";

    root.append(summary, body);

    return { root, body, label, pending: [], reasoningEl: null, toolCount: 0 };
}

function liveSetStatus(trace, text, spinning) {
    if (!trace) return;
    trace.label.textContent = text;
    trace.root.classList.toggle("working", Boolean(spinning));
}

function liveAddRound(trace, round, max) {
    if (!trace) return;
    // 改代码模式有 20 轮，标出「3/20」用户才知道还剩多少、要不要收尾
    const total = Number(max) > 0 ? `/${max}` : "";
    const label = round === 1 ? `第 1${total} 轮 · 理解问题` : `第 ${round}${total} 轮 · 继续分析`;
    const row = document.createElement("div");
    row.className = "trace-live-round";
    row.textContent = label;
    trace.body.appendChild(row);
    trace.body.scrollTop = trace.body.scrollHeight;
    liveSetStatus(trace, label, true);
}

function liveToolStart(trace, name, args) {
    if (!trace) return;
    const row = document.createElement("div");
    row.className = "trace-live-item running";

    const icon = document.createElement("span");
    icon.className = "trace-live-icon";
    icon.textContent = "◐";

    const body = document.createElement("div");
    body.className = "trace-live-body";

    const title = document.createElement("div");
    title.className = "trace-live-title";
    title.textContent = toolLabel(name);

    const detail = document.createElement("div");
    detail.className = "trace-live-sub";
    const described = describeArgs(args);
    detail.textContent = described || "正在执行…";

    body.append(title, detail);
    row.append(icon, body);
    trace.body.appendChild(row);

    trace.toolCount += 1;
    trace.pending.push({ name: name, row: row, icon: icon });
    trace.body.scrollTop = trace.body.scrollHeight;

    liveSetStatus(trace, "正在" + toolLabel(name) + "…", true);
}

function liveToolEnd(trace, name, ok, elapsedMs, preview) {
    if (!trace) return;
    const index = trace.pending.findIndex(item => item.name === name);
    const target = index >= 0 ? trace.pending.splice(index, 1)[0] : null;
    if (!target) return;

    target.row.classList.remove("running");
    target.row.classList.add(ok ? "ok" : "failed");
    target.icon.textContent = ok ? "✓" : "!";

    const title = target.row.querySelector(".trace-live-title");
    if (title) {
        const time = document.createElement("span");
        time.className = "trace-live-time";
        time.textContent = elapsedMs == null ? "" : (elapsedMs >= 1000
            ? (elapsedMs / 1000).toFixed(1) + " 秒"
            : elapsedMs + " 毫秒");
        title.appendChild(time);
    }

    // 结果摘要：让用户看到「查到了什么」，而不是只知道调用了工具
    const snippet = String(preview || "").replace(/\s+/g, " ").trim();
    if (snippet) {
        const result = document.createElement("div");
        result.className = "trace-live-result";
        result.textContent = (ok ? "结果：" : "未成功：") + (snippet.length > 110 ? snippet.slice(0, 110) + "…" : snippet);
        target.row.querySelector(".trace-live-body")?.appendChild(result);
    }

    trace.body.scrollTop = trace.body.scrollHeight;
    liveSetStatus(trace, (ok ? "已完成 " : "未成功 ") + toolLabel(name), true);
}

// 一段 unified diff → 红绿行。折叠标题就是「改了哪个文件、增删几行」。
// 传入的 edit 结构：{ path, added, removed, created, diff }
// 任务清单卡片：实时过程面板与历史过程面板共用。
// 只认三种状态，图标比文字更快认出来：✓ 完成 / ▸ 进行中 / ○ 待办。
function renderTodoCard(items, doneCount, totalCount, compact) {
    const list = Array.isArray(items) ? items : [];
    const total = Number(totalCount) > 0 ? Number(totalCount) : list.length;
    const done = Number.isFinite(Number(doneCount))
        ? Number(doneCount)
        : list.filter(item => item && item.status === "done").length;

    const card = document.createElement("div");
    card.className = "todo-card" + (compact ? " compact" : "");

    const head = document.createElement("div");
    head.className = "todo-head";

    const title = document.createElement("span");
    title.className = "todo-title";
    title.textContent = "任务清单";

    const count = document.createElement("span");
    count.className = "todo-count";
    count.textContent = done + "/" + total + (done >= total && total ? " 完成" : "");

    head.append(title, count);

    const bar = document.createElement("div");
    bar.className = "todo-bar";
    const fill = document.createElement("span");
    fill.style.width = (total ? Math.round(done / total * 100) : 0) + "%";
    bar.appendChild(fill);

    const ul = document.createElement("ul");
    ul.className = "todo-list";
    list.forEach(item => {
        const status = item && item.status === "done" ? "done"
            : item && item.status === "active" ? "active" : "pending";
        const li = document.createElement("li");
        li.className = "todo-item " + status;

        const icon = document.createElement("span");
        icon.className = "todo-icon";
        icon.textContent = status === "done" ? "✓" : status === "active" ? "▸" : "○";

        const text = document.createElement("span");
        text.className = "todo-text";
        text.textContent = (item && item.text) || "";

        li.append(icon, text);
        ul.appendChild(li);
    });

    card.append(head, bar, ul);
    return card;
}

function renderDiffBlock(edit, open) {
    const details = document.createElement("details");
    details.className = "edit-diff";
    details.open = Boolean(open);

    const summary = document.createElement("summary");
    const label = document.createElement("span");
    label.className = "edit-path";
    label.textContent = (edit.created ? "新建 " : "改动 ") + (edit.path || "");
    const stat = document.createElement("span");
    stat.className = "edit-stat";
    stat.textContent = "+" + (Number(edit.added) || 0) + " −" + (Number(edit.removed) || 0);
    summary.append(label, stat);
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "diff-body";
    const lines = String(edit.diff || "").split("\n");
    // 行数太多时只画前 400 行，避免一条消息塞几千个节点把页面拖慢
    lines.slice(0, 400).forEach(line => {
        const row = document.createElement("div");
        let cls = "diff-line";
        if (line.startsWith("+++") || line.startsWith("---")) cls += " diff-meta";
        else if (line.startsWith("@@")) cls += " diff-hunk";
        else if (line.startsWith("+")) cls += " diff-add";
        else if (line.startsWith("-")) cls += " diff-del";
        row.className = cls;
        row.textContent = line || " ";
        body.appendChild(row);
    });
    if (lines.length > 400) {
        const more = document.createElement("div");
        more.className = "diff-line diff-meta";
        more.textContent = "…（还有 " + (lines.length - 400) + " 行未显示）";
        body.appendChild(more);
    }
    details.appendChild(body);
    return details;
}

// 流式过程中收到 file_edit：把 diff 挂到刚建的那条工具记录里，
// 用户不用等回答结束就能看到改了哪几行。
function liveTodos(trace, event) {
    if (!trace || !Array.isArray(event.items)) return;
    const card = renderTodoCard(event.items, event.done, event.total, true);
    if (trace.todoEl && trace.todoEl.parentNode) {
        // 就地替换：卡片一直待在面板顶部，不因刷新而跳动
        trace.todoEl.replaceWith(card);
    } else {
        trace.body.insertBefore(card, trace.body.firstChild);
    }
    trace.todoEl = card;
    trace.body.scrollTop = trace.body.scrollHeight;
}

function liveFileEdit(trace, event) {
    if (!trace) return;
    const pending = (trace.pending || []).find(item => item.name === event.tool);
    const host = pending ? pending.row.querySelector(".trace-live-body") : null;
    (host || trace.body).appendChild(renderDiffBlock(event, true));
    trace.body.scrollTop = trace.body.scrollHeight;
    liveSetStatus(trace, "已修改 " + (event.path || "文件"), true);
}

function liveReasoning(trace, text) {
    if (!trace || !text) return;
    if (!trace.reasoningEl) {
        const row = document.createElement("div");
        row.className = "trace-live-item reasoning";

        const icon = document.createElement("span");
        icon.className = "trace-live-icon";
        icon.textContent = "✻";

        const body = document.createElement("div");
        body.className = "trace-live-body";

        const title = document.createElement("div");
        title.className = "trace-live-title";
        title.textContent = "模型思考";

        // 用 div 而不是 pre：全局 pre 样式是深色代码块，会把思考内容渲染成一坨黑底
        const pre = document.createElement("div");
        pre.className = "trace-live-reasoning";

        body.append(title, pre);
        row.append(icon, body);
        trace.body.appendChild(row);

        trace.reasoningEl = pre;
    }
    trace.reasoningEl.textContent += text;
    trace.body.scrollTop = trace.body.scrollHeight;
    liveSetStatus(trace, "思考中…", true);
}

// 需要确认的命令：在过程面板里插一张卡片，等你点「允许」或「拒绝」。
// 不点就等于拒绝：后端等到超时会自己放弃，不会替你按下回车。
function liveCommandAsk(trace, event) {
    if (!trace) return;
    const card = document.createElement("div");
    card.className = "cmd-ask";
    card.dataset.approvalId = event.approval_id || "";

    const head = document.createElement("div");
    head.className = "cmd-ask-head";
    head.textContent = "这条命令要先经你同意";

    const code = document.createElement("div");
    code.className = "cmd-ask-cmd";
    code.textContent = "$ " + (event.command || "");

    const why = document.createElement("p");
    why.className = "cmd-ask-why";
    why.textContent = (event.reason || "需要确认") + `（目录 ${event.cwd || "."}）`;

    const bar = document.createElement("div");
    bar.className = "cmd-ask-actions";

    const allow = document.createElement("button");
    allow.type = "button";
    allow.className = "cmd-allow";
    allow.textContent = "允许执行";
    allow.onclick = () => answerCommand(event, true);

    const deny = document.createElement("button");
    deny.type = "button";
    deny.className = "cmd-deny";
    deny.textContent = "拒绝";
    deny.onclick = () => answerCommand(event, false);

    const hint = document.createElement("span");
    hint.className = "cmd-ask-hint";
    hint.textContent = event.wait_seconds ? `${event.wait_seconds} 秒内不选就算拒绝` : "";

    bar.append(allow, deny, hint);
    card.append(head, code, why, bar);
    trace.body.appendChild(card);
    trace.body.scrollTop = trace.body.scrollHeight;
    liveSetStatus(trace, "等你确认一条命令", true);
}

async function answerCommand(event, allow) {
    const card = document.querySelector(`.cmd-ask[data-approval-id="${event.approval_id}"]`);
    if (!card || card.dataset.done === "1") return;
    card.dataset.done = "1";
    card.querySelectorAll("button").forEach(btn => { btn.disabled = true; });
    const hint = card.querySelector(".cmd-ask-hint");
    if (hint) hint.textContent = allow ? "已允许，正在执行…" : "已拒绝";
    card.classList.add(allow ? "allowed" : "denied");
    const runId = event.run_id || (activeStream && activeStream.runId) || "";
    try {
        await apiJson(`/chat/approve/${encodeURIComponent(runId)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approval_id: event.approval_id, allow: Boolean(allow) })
        });
    } catch (err) {
        // 后端已经放弃这条审批（超时）时不要装作成功，把按钮放开让你看清状态
        if (hint) hint.textContent = "确认没送到后端：" + (err.message || "请重试");
        card.dataset.done = "0";
        card.querySelectorAll("button").forEach(btn => { btn.disabled = false; });
    }
}

function liveCommandVerdict(trace, event) {
    const card = document.querySelector(`.cmd-ask[data-approval-id="${event.approval_id}"]`);
    if (!card) return;
    const labels = {
        approved: "已允许", denied: "已拒绝",
        timeout: "超时没有确认，已放弃", cancelled: "已取消"
    };
    if (event.verdict === "approved" || event.verdict === "denied") return;
    card.dataset.done = "1";
    card.querySelectorAll("button").forEach(btn => { btn.disabled = true; });
    card.classList.add("denied");
    const hint = card.querySelector(".cmd-ask-hint");
    if (hint) hint.textContent = labels[event.verdict] || "未获批准";
}

function liveNote(trace, text, kind) {
    if (!trace) return;
    liveSetStatus(trace, text, false);
    // 限制类提示已经显示在上面一行了，正文里不再重复；错误和「被退回自测」要留痕，
    // 因为它们解释了「为什么回答还没结束」。
    if (kind !== "error" && kind !== "verify") return;
    const row = document.createElement("div");
    row.className = "trace-live-note" + (kind ? " " + kind : "");
    row.textContent = text;
    trace.body.appendChild(row);
    trace.body.scrollTop = trace.body.scrollHeight;
}

// 正在进行的流式请求：{ controller, runId, stopped, timer }
let activeStream = null;


// 读取 SSE：后端每帧是 "data: {json}\n\n"
async function streamChat(payload, onEvent, signal) {

    const res = await apiFetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: signal
    });

    if (!res.ok) {

        const data = await parseJson(res);

        const err = new Error(httpErrorText(res, data));

        err.status = res.status;

        err.tips = httpErrorTips(res.status);

        throw err;

    }

    if (!res.body || typeof res.body.getReader !== "function") {

        throw new Error("当前浏览器不支持流式读取，请换用新版 Chrome 或 Edge。");

    }

    const reader = res.body.getReader();

    const decoder = new TextDecoder("utf-8");

    let buffer = "";

    while (true) {

        const chunk = await reader.read();

        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });

        let cut = buffer.indexOf("\n\n");

        while (cut !== -1) {

            const frame = buffer.slice(0, cut);

            buffer = buffer.slice(cut + 2);

            const line = frame.split("\n").find(item => item.startsWith("data:"));

            if (line) {

                const raw = line.slice(5).trim();

                if (raw) {

                    let event = null;

                    try {

                        event = JSON.parse(raw);

                    } catch (e) {

                        event = null;

                    }

                    if (event) onEvent(event);

                }

            }

            cut = buffer.indexOf("\n\n");

        }

    }

}


// 发送按钮在「发送」和「停止」之间切换
function setStopUI(streaming) {

    const btn = $("sendBtn");

    if (!btn) return;

    if (streaming) {

        if (!btn.dataset.idleHtml) btn.dataset.idleHtml = btn.innerHTML;

        btn.disabled = false;

        btn.classList.add("stopping");

        btn.innerHTML = '停止<span class="send-arrow">■</span>';

        btn.onclick = stopGeneration;

    } else {

        btn.classList.remove("stopping");

        if (btn.dataset.idleHtml) btn.innerHTML = btn.dataset.idleHtml;

        btn.disabled = false;

        btn.onclick = sendMessage;

    }

}


// 点「停止」：先通知后端中断，再等它把 done 回过来；超时才强制断开
function stopGeneration() {

    if (!activeStream || activeStream.stopped) return;

    activeStream.stopped = true;

    const btn = $("sendBtn");

    if (btn) {

        btn.disabled = true;

        btn.innerHTML = "正在停止…";

    }

    if (activeStream.runId) {

        apiFetch("/chat/cancel/" + encodeURIComponent(activeStream.runId), { method: "POST" })
            .catch(() => { });

    }

    activeStream.timer = setTimeout(() => {

        if (activeStream) activeStream.controller.abort();

    }, 4000);

}


// 收尾：把流式纯文本换成 Markdown，并把过程面板定格成完整时间线
// ---------------------------------------------------------------- 来源链接

// 来源最后会变成 <a href>，只放行 http(s)：否则一个 javascript: 网址就是一个洞
function isSafeSourceUrl(url) {
    return /^https?:\/\//i.test(String(url || ""));
}

function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return ""; }
}

// 流式回答时来源在 live.sources 里；回看历史时来源埋在 steps 的 result_data 里。
// 两条路都走这里，渲染出来才一致。
function collectSources(live, event) {
    const seen = new Set();
    const out = [];
    const push = item => {
        const url = item && item.url;
        if (!isSafeSourceUrl(url) || seen.has(url)) return;
        seen.add(url);
        out.push({ title: String(item.title || url), url: String(url) });
    };
    ((live && live.sources) || []).forEach(push);
    (Array.isArray(event && event.steps) ? event.steps : []).forEach(step => {
        const data = step && step.result_data;
        if (data && Array.isArray(data.sources)) data.sources.forEach(push);
        // B 站搜索结果不发 sources 事件，url 在 videos 里。不补这一段，回看历史时
        // 视频卡片会整块消失，用户只能看到正文里的文字标题。
        if (data && Array.isArray(data.videos)) {
            data.videos.forEach(v => push({ title: "视频：" + (v.title || ""), url: v.url }));
        }
    });
    return out.slice(0, 8);
}

function renderSources(items) {
    const wrap = document.createElement("div");
    wrap.className = "source-list";
    const head = document.createElement("div");
    head.className = "source-head";
    head.textContent = "参考来源 · " + items.length;
    wrap.appendChild(head);
    items.forEach((item, index) => {
        const link = document.createElement("a");
        link.className = "source-item";
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const num = document.createElement("span");
        num.className = "source-idx";
        num.textContent = String(index + 1);
        const body = document.createElement("span");
        body.className = "source-body";
        const title = document.createElement("span");
        title.className = "source-title";
        title.textContent = item.title;
        const domain = document.createElement("span");
        domain.className = "source-domain";
        domain.textContent = hostOf(item.url);
        body.appendChild(title);
        body.appendChild(domain);
        link.appendChild(num);
        link.appendChild(body);
        wrap.appendChild(link);
    });
    return wrap;
}

function finalizeStream(box, live, answer, event, buffer, question) {

    const hasSteps = Array.isArray(event.steps) && event.steps.length;

    if (live && live.root && live.root.parentNode) {

        if (hasSteps) {
            // 过程面板就地替换：位置不变，用户的视线不用重新找
            live.root.replaceWith(
                renderAgentTrace(event.steps, event.status, event.rounds, event.duration_ms, true, event.run_id)
            );
        } else {
            live.root.remove();
        }

    } else if (hasSteps) {

        box.insertBefore(
            renderAgentTrace(event.steps, event.status, event.rounds, event.duration_ms, true, event.run_id),
            box.firstChild
        );

    }

    // 后端可能因为中断而返回空字符串，这时用已经流式收到的内容兜底
    let reply = (event.reply === undefined || event.reply === null || event.reply === "")
        ? buffer
        : String(event.reply);

    // done 里的 reply 只包含最后一次模型输出。如果前面还有已经展示过的过渡语句，
    // 收尾时改用完整 buffer，避免文字突然变少。
    if (buffer && reply && buffer.length > reply.length && buffer.trim().endsWith(reply.trim())) {

        reply = buffer;

    }

    if (activeMode === "chat" && isMusicReply(reply)) {

        answer.innerHTML = createMusicCard(reply);

    } else {

        answer.innerHTML = renderMarkdown(reply);

    }

    // 来源挂在回答下面：上面那段文字就是这些网页的结论，用户能点开核对。
    // 位置在 markdown 之后、警示条之前——警示条说的是「这份回答可信度如何」，
    // 应该排在最后，离眼睛最近。
    const sources = collectSources(live, event);

    if (sources.length) box.appendChild(renderSources(sources));

    // 自测回路：改过代码却没有一条命令跑成功。这不算失败，但结论不能当验证过用，
    // 所以在回答下面挂一条明确警示，而不是让模型自己一句「应该没问题」带过去。
    const selfCheck = event.self_check;

    if (selfCheck && selfCheck.pending) {

        const files = selfCheck.edited || [];

        const shown = files.slice(0, 3).join("、") + (files.length > 3 ? " 等" : "");

        const warn = document.createElement("div");

        warn.className = "verify-warn";

        warn.textContent = "未验证：" + files.length + " 个代码文件已改动"
            + (shown ? "（" + shown + "）" : "")
            + "，但没有检测到成功的命令。可用性请自行确认后再使用。";

        box.appendChild(warn);

    }

    if (event.status === "cancelled") {

        const note = document.createElement("div");

        note.className = "stream-note";

        note.textContent = "已停止生成";

        box.appendChild(note);

    } else if (event.status === "error") {

        const note = document.createElement("div");

        note.className = "stream-error";

        note.textContent = "本次回答未完成。可以到右上角「模型与 API Key」检查配置后重试。";

        box.appendChild(note);

    }

    const titleEl = $("currentTitle");

    if (titleEl) titleEl.textContent = question.slice(0, 15);

    loadChats();

    if (allowWrites) {

        loadMemories();

        loadLearningPlans();

    }

    const chat = $("chat");

    if (chat) chat.scrollTop = chat.scrollHeight;

}


async function sendMessage() {

    if (activeStream) return;

    const input = $("input");

    if (!input) return;

    const text = input.value.trim();

    if (!text) return;

    const selectedFilename = $("fileName")?.value?.trim() || "";
    if (activeMode === "file" && !selectedFilename) {
        showModeStatus("请先添加资料，或从已上传资料中选择一个文件。", "error");
        return;
    }
    if (activeMode === "file" && !knownFiles.has(selectedFilename)) {
        showModeStatus("这份资料还没有上传，请先点击“上传资料”。", "error");
        return;
    }
    if (activeMode === "paper" && !paperReady) {
        showModeStatus("请先选择并解析一篇 PDF 论文。", "error");
        return;
    }

    const chat = $("chat");

    if (!chat) return;

    // 显示用户消息
    addMessage("user", text);

    input.value = "";
    input.style.height = "auto";

    // 回答容器先挂上去，第一个 token 一到就能画出来
    const box = document.createElement("div");

    box.className = "ai";

    // 过程面板挂在回答上方：先看到「他做了什么」，再看到答案
    const live = createLiveTrace();

    const answer = document.createElement("div");

    answer.className = "agent-answer";

    box.appendChild(live.root);

    box.appendChild(answer);

    chat.appendChild(box);

    chat.scrollTop = chat.scrollHeight;

    const controller = new AbortController();

    activeStream = { controller: controller, runId: null, stopped: false, timer: null };

    setStopUI(true);

    let buffer = "";

    let finished = false;

    let pending = false;

    const paint = () => {

        pending = false;

        answer.textContent = buffer;

        chat.scrollTop = chat.scrollHeight;

    };

    try {

        // 没有当前会话就先建一个
        const chatId = await ensureChatId();

        await streamChat(
            {
                text: text,
                chat_id: chatId,
                mode: activeMode || "chat",
                filename: activeMode === "file" ? selectedFilename : null,
                allow_writes: allowWrites,
                allow_workspace: allowWorkspace,
                allow_code_edit: allowCodeEdit,
                allow_command: allowCommand,
                show_thinking: showThinking
            },
            event => {

                switch (event.type) {

                    case "start":
                        if (activeStream) activeStream.runId = event.run_id;
                        break;

                    case "round":
                        liveAddRound(live, event.round, event.max_rounds);
                        break;

                    case "reasoning":
                        liveReasoning(live, event.text);
                        break;

                    case "tool_start":
                        liveToolStart(live, event.tool, event.args);
                        break;

                    case "command_ask":
                        liveCommandAsk(live, event);
                        break;

                    case "command_verdict":
                        liveCommandVerdict(live, event);
                        break;

                    case "file_edit":
                        liveFileEdit(live, event);
                        break;

                    case "todos":
                        liveTodos(live, event);
                        break;

                    // 来源链接先攒着，等回答渲染完再挂上去。
                    // 中途插进去会被后面流出来的文字挤走，位置就乱了。
                    case "sources":
                        (live.sources = live.sources || []).push(...(event.items || []));
                        break;

                    // 改了代码却一条命令都没跑通，后端把「收尾」退回去了。
                    // 已经流出来的那版是草稿：清掉 buffer，最终只保留验证后的回答，
                    // 否则正文里会出现「初稿 + 正式回答」两段重复内容。
                    case "verify_required":
                        liveNote(live, "改动还没验证，已要求先跑一条测试", "verify");
                        buffer = "";
                        break;

                    case "tool_end":
                        liveToolEnd(live, event.tool, event.ok, event.elapsed_ms, event.preview);                        // 多轮之间留一个空行，免得「我查一下…」和正式回答黏在一起
                        if (buffer.trim() && !buffer.endsWith("\n\n")) {

                            buffer += "\n\n";

                            if (!pending) {

                                pending = true;

                                requestAnimationFrame(paint);

                            }

                        }
                        break;

                    case "token":
                        if (activeStream && activeStream.stopped) break;
                        buffer += event.text;
                        if (!pending) {

                            pending = true;

                            requestAnimationFrame(paint);

                        }
                        break;

                    case "limit":
                        liveNote(live, event.reason || "达到保护限制", "limit");
                        break;

                    case "error":
                        liveNote(live, event.message || "回答中断", "error");
                        break;

                    case "done":
                        finished = true;
                        finalizeStream(box, live, answer, event, buffer, text);
                        break;

                }

            },
            controller.signal
        );

    } catch (err) {

        if (err && err.name === "AbortError") {

            liveSetStatus(live, "已停止", false);

        } else {

            liveNote(live, "请求失败：" + ((err && err.message) || "未知错误"), "error");

        }

    } finally {

        if (activeStream && activeStream.timer) clearTimeout(activeStream.timer);

        if (!finished && buffer) {

            answer.innerHTML = renderMarkdown(buffer);

        }

        // 请求异常结束时面板还停在「正在…」，收尾成一句话，不留空转的转圈
        if (!finished && live.root && live.root.parentNode) {
            liveSetStatus(live, live.toolCount ? "本次回答未完整结束" : "未完成", false);
        }

        activeStream = null;

        setStopUI(false);

    }

}

// =============================
// 十、添加消息
// =============================

function addMessage(role, text) {

    const box = $("chat");

    if (!box) return;

    const div = document.createElement("div");

    const welcome = $("welcome");
    if (welcome) welcome.remove();

    div.className = role;


    if (role === "ai") {

        const str = (text === null || text === undefined)
            ? ""
            : String(text);

        if (isMusicReply(str)) {

            div.innerHTML = createMusicCard(str);

        } else {

            div.innerHTML = renderMarkdown(str);

        }

    } else {

        div.innerText = text;

    }


    box.appendChild(div);

    box.scrollTop = box.scrollHeight;

}



// =============================
// 十一、AI逐字显示
// =============================

function typeWriter(element, text, speed = 30) {

    const str = (text === null || text === undefined)
        ? ""
        : String(text);

    let index = 0;

    element.innerHTML = "";

    const timer = setInterval(() => {

        element.innerHTML = renderMarkdown(
            str.substring(0, index)
        );

        index++;

        if (index > str.length) {

            clearInterval(timer);

        }

    }, speed);

}

// 用户展开过一次过程面板，之后的消息就默认展开。只是本机偏好，不上传。
function traceOpenPref() {
    try { return localStorage.getItem("agentTraceOpen") === "1"; } catch (_) { return false; }
}

function traceTimeLabel(elapsedMs) {
    if (elapsedMs == null) return "";
    return elapsedMs >= 1000 ? (elapsedMs / 1000).toFixed(1) + " 秒" : elapsedMs + " 毫秒";
}

function renderAgentTrace(steps, status, rounds, durationMs, forceOpen, runId) {
    const details = document.createElement("details");
    details.className = "agent-trace";
    details.open = forceOpen === undefined ? traceOpenPref() : Boolean(forceOpen);
    details.addEventListener("toggle", () => {
        try { localStorage.setItem("agentTraceOpen", details.open ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }
    });

    const summary = document.createElement("summary");
    const tools = steps.filter(step => step.kind === "tool_call");
    // 清单可能被更新过多次，历史里只呈现最后一次的完整快照
    const todoStep = steps.reduce((last, step) => (
        step.kind === "tool_call" && step.ok
            && step.result_data && Array.isArray(step.result_data.items)
            && step.result_data.items.length ? step : last), null);
    const thoughts = steps.filter(step => step.kind === "llm_call" && step.reasoning);
    const seconds = ((durationMs || 0) / 1000).toFixed(1);
    const pieces = [];
    if (tools.length) pieces.push(`调用了 ${tools.length} 个工具`);
    if (todoStep) {
        const todo = todoStep.result_data;
        pieces.push(`任务 ${todo.done || 0}/${todo.total || todo.items.length}`);
    }
    pieces.push(`${rounds || 1} 轮`);
    if (thoughts.length) pieces.push("含思考过程");
    pieces.push(`${seconds} 秒`);
    summary.textContent = "⚙　" + pieces.join(" · ");
    details.appendChild(summary);

    const list = document.createElement("ol");
    list.className = "agent-trace-list";

    steps.forEach(step => {
        const item = document.createElement("li");
        const title = document.createElement("strong");

        if (step.kind === "tool_call") {
            title.textContent = (step.ok ? "✓ " : "! ") + toolLabel(step.tool);
            item.className = step.ok ? "trace-ok" : "trace-error";
            item.appendChild(title);

            const time = traceTimeLabel(step.elapsed_ms);
            if (time) {
                const span = document.createElement("span");
                span.className = "trace-time";
                span.textContent = time;
                item.appendChild(span);
            }

            const described = describeArgs(step.args);
            if (described) {
                const line = document.createElement("div");
                line.className = "trace-detail";
                line.textContent = described;
                item.appendChild(line);
            }

            if (step.result_preview) {
                // 结果默认折叠：过程要看得见，但不能把回答挤出屏幕
                const box = document.createElement("details");
                box.className = "trace-result";
                const head = document.createElement("summary");
                head.textContent = step.ok ? "查看返回内容" : "查看失败原因";
                const preview = document.createElement("p");
                preview.className = "trace-preview";
                preview.textContent = step.result_preview;
                box.append(head, preview);
                item.appendChild(box);
            }

            // 写入类工具在步骤里存了完整 diff，刷新后也能回看改了什么
            const edit = step.result_data && step.result_data.diff ? step.result_data : null;
            if (edit) item.appendChild(renderDiffBlock(edit, false));

        } else if (step.kind === "llm_call") {
            title.textContent = `第 ${step.round_no || 1} 轮 · ${step.reasoning ? "模型思考" : "分析"}`;
            item.className = "trace-think-item";
            item.appendChild(title);

            const time = traceTimeLabel(step.elapsed_ms);
            if (time) {
                const span = document.createElement("span");
                span.className = "trace-time";
                span.textContent = time;
                item.appendChild(span);
            }

            if (step.reasoning) {
                const think = document.createElement("p");
                think.className = "trace-think";
                think.textContent = step.reasoning;
                item.appendChild(think);
            }

        } else if (step.kind === "verify_gate") {
            title.textContent = "改动未验证，退回要求先跑测试";
            item.className = "trace-verify";
            item.appendChild(title);
            const reason = document.createElement("p");
            reason.textContent = step.detail || "改了代码，但没有一条命令跑成功。";
            item.appendChild(reason);
        } else if (step.kind === "limit") {
            title.textContent = "达到保护限制";
            item.className = "trace-limit";
            item.appendChild(title);
            const reason = document.createElement("p");
            reason.textContent = step.reason || step.detail || "本次运行已安全停止。";
            item.appendChild(reason);
        } else if (step.kind === "error") {
            title.textContent = "处理遇到问题";
            item.className = "trace-error";
            item.appendChild(title);
            const reason = document.createElement("p");
            reason.textContent = step.detail || "模型调用失败。";
            item.appendChild(reason);
        } else if (step.kind === "final") {
            title.textContent = "整理最终回答";
            item.appendChild(title);
        } else {
            title.textContent = "分析问题";
            item.appendChild(title);
        }

        list.appendChild(item);
    });

    if (todoStep) {
        details.appendChild(renderTodoCard(
            todoStep.result_data.items,
            todoStep.result_data.done,
            todoStep.result_data.total,
            false
        ));
    }

    details.appendChild(list);

    // 有文件改动就提供「一键撤销」。依据运行记录里的备份逆序还原，新建的文件会被删除。
    const edited = steps.filter(step => step.kind === "tool_call" && step.ok
        && step.result_data && step.result_data.diff);
    if (edited.length && runId) {
        const bar = document.createElement("div");
        bar.className = "edit-undo-bar";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "edit-undo";
        button.textContent = "撤销这次改动（" + edited.length + " 处）";
        button.addEventListener("click", () => undoRunEdits(runId, button));
        bar.appendChild(button);
        details.appendChild(bar);
    }

    if (status && status !== "ok") {
        const notice = document.createElement("div");
        notice.className = "trace-status";
        notice.textContent = status === "max_rounds" ? "已达到最大工具调用轮数，回答可能不完整。" :
            status === "timeout" ? "本次运行达到时间上限，回答基于已有资料。" :
                status === "cancelled" ? "你停止了本次回答，上面是已经生成的部分。" :
                    "本次运行未能完整完成。";
        details.appendChild(notice);
    }
    return details;
}

// 撤销一次运行里的文件改动。后端按备份逆序还原；被用户后来改过的文件会跳过。
async function undoRunEdits(runId, button) {
    if (!runId || !button) return;
    if (!confirm("把这次运行改过的文件还原到运行前？新建的文件会被删除，运行后又改过的文件会被跳过。")) return;

    button.disabled = true;
    button.textContent = "正在撤销…";
    try {
        const data = await apiJson("/code/undo/" + encodeURIComponent(runId), { method: "POST" });
        const parts = [];
        if ((data.restored || []).length) parts.push("还原 " + data.restored.length + " 个文件");
        if ((data.removed || []).length) parts.push("删除 " + data.removed.length + " 个新建文件");
        if ((data.skipped || []).length) parts.push("跳过 " + data.skipped.length + " 个（" + (data.skipped[0].reason || "") + "）");
        button.textContent = parts.length ? parts.join("，") : (data.message || "没有需要撤销的改动");
        button.classList.add("done");
    } catch (err) {
        button.disabled = false;
        button.textContent = "撤销失败：" + ((err && err.message) || "未知错误");
    }
}



// =============================
// 十二、清空聊天
// =============================

function clearChat() {

    const box = $("chat");

    if (box) {

        box.innerHTML = "";

        if ($("currentTitle")) $("currentTitle").textContent = "新对话";

        box.innerHTML = '<div id="welcome" class="welcome"><div class="welcome-orb"><i></i><i></i><i></i></div><div class="eyebrow">YOUR THINKING PARTNER</div><h1>你好，今天想用 AI 做什么？</h1><p>先选择一个功能，接下来的提问和回答都会在这里进行。</p><div class="mode-grid"><button class="mode-card" onclick="selectMode(\'chat\')"><span class="mode-icon blue">✳</span><span><b>智能对话</b><small>自由提问、灵感探索与音乐推荐</small></span><span class="mode-arrow">↗</span></button><button class="mode-card" onclick="selectMode(\'file\')"><span class="mode-icon purple">▤</span><span><b>文件问答</b><small>上传文档或图片，提取文字并理解图像</small></span><span class="mode-arrow">↗</span></button><button class="mode-card" onclick="selectMode(\'paper\')"><span class="mode-icon amber">论</span><span><b>论文助手</b><small>解析 PDF 论文，检索内容并深入讨论</small></span><span class="mode-arrow">↗</span></button></div></div>';
        activeMode = null;
        if ($("modeBar")) $("modeBar").hidden = true;
        if ($("input")) { $("input").disabled = true; $("input").placeholder = "先选择一个功能…"; $("input").value = ""; }
        if ($("sendBtn")) $("sendBtn").disabled = true;
        if ($("composerHint")) $("composerHint").textContent = "选择功能后即可开始";
        if ($("filePickerLabel")) $("filePickerLabel").hidden = true;
        if ($("paperPickerLabel")) $("paperPickerLabel").hidden = true;
        if ($("paperStatus")) $("paperStatus").hidden = true;
        if ($("uploadPaperBtn")) $("uploadPaperBtn").hidden = true;
        if ($("fileList")) $("fileList").hidden = true;
        if ($("contextName")) $("contextName").textContent = "";

    }

}

let activeMode = null;

const MODE_INFO = {
    chat: { name: "智能对话", icon: "✳", placeholder: "发送消息…", hint: "Enter 发送 · Shift + Enter 换行", greeting: "你好，我可以陪你一起探索问题、整理思路，也可以根据你的心情推荐音乐。" },
    file: { name: "文件问答", icon: "▤", placeholder: "针对这份资料或图片提问…", hint: "支持 PDF、DOCX、TXT、JPG、PNG、WEBP；图片最大 10 MB", greeting: "请先添加或选择一份资料或图片，然后告诉我你想了解什么。" },
    paper: { name: "论文助手", icon: "论", placeholder: "询问论文内容…", hint: "回答将基于已解析的论文内容", greeting: "请先选择并解析一篇 PDF 论文，再开始提问。" }
};

function selectMode(mode) {
    const info = MODE_INFO[mode];
    if (!info) return;
    activeMode = mode;
    if ($("paperStatus")) $("paperStatus").hidden = true;
    if ($("welcome")) $("welcome").remove();
    if ($("modeBar")) $("modeBar").hidden = false;
    if ($("activeModeName")) $("activeModeName").textContent = info.name;
    if ($("activeModeIcon")) $("activeModeIcon").textContent = info.icon;
    if ($("contextName")) {
        const context = mode === "file" ? ($("fileName")?.value || "") : mode === "paper" ? ($("paperInput")?.files?.[0]?.name || "") : "";
        $("contextName").textContent = context;
    }
    if ($("modePicker")) $("modePicker").hidden = true;
    if ($("input")) { $("input").disabled = false; $("input").placeholder = info.placeholder; }
    if ($("sendBtn")) $("sendBtn").disabled = false;
    if ($("composerHint")) $("composerHint").textContent = info.hint;
    if ($("filePickerLabel")) $("filePickerLabel").hidden = mode !== "file";
    if ($("fileList")) $("fileList").hidden = mode !== "file";
    if ($("paperPickerLabel")) $("paperPickerLabel").hidden = mode !== "paper";
    if ($("uploadFileBtn")) $("uploadFileBtn").hidden = mode !== "file" || !$("fileInput")?.files?.length;
    if ($("uploadPaperBtn")) $("uploadPaperBtn").hidden = mode !== "paper" || !$("paperInput")?.files?.length;
    if (mode === "file") {
        loadFiles();
        if ($("fileInput")?.files?.length) uploadFile();
    }
    if (mode === "paper") checkPaperStatus();
    if (!currentChatId) ensureChatId().catch(error => showModeStatus(error.message, "error"));
    if (!$("chat").querySelector(".user, .ai")) addMessage("ai", info.greeting);
}

function toggleModePicker() {
    const picker = $("modePicker");
    if (picker) picker.hidden = !picker.hidden;
}

function toggleHistory(force) {
    const panel = $("historyPanel");
    if (!panel) return;
    panel.hidden = force === undefined ? !panel.hidden : !force;
    if (!panel.hidden) loadChats();
}

function showModeStatus(message, type = "") {
    const status = $("paperStatus");
    if (!status) return;
    status.textContent = message;
    status.className = "upload-status" + (type ? " " + type : "");
    status.hidden = !message;
    if (type === "success" || type === "error") setTimeout(() => { status.hidden = true; }, 5000);
}

async function checkPaperStatus() {
    if ($("paperInput")?.files?.length) {
        paperReady = false;
        showModeStatus("所选论文尚未解析，请点击“解析论文”。");
        return;
    }
    try {
        const data = await apiJson("/paper_status");
        paperReady = Boolean(data && data.ready);
        showModeStatus(paperReady ? "已载入论文索引，可以直接提问。" : "请先选择并解析一篇 PDF 论文。");
    } catch (error) {
        paperReady = false;
        showModeStatus("暂时无法读取论文状态：" + error.message, "error");
    }
}



// =============================
// 十三、上传文件
// =============================

async function uploadFile() {

    const input = $("fileInput");

    const file =
        (input && input.files)
            ? input.files[0]
            : null;


    if (!file) {

        showModeStatus("请先添加一份资料。", "error");

        return;

    }



    let form = new FormData();

    form.append("file", file);


    setBtnLoading("uploadFileBtn", true, "上传中…");


    try {

        const data = await apiJson("/upload", {

            method: "POST",

            body: form

        });


        const filename = data.filename || file.name;
        if ($("fileName")) $("fileName").value = filename;
        if ($("contextName")) $("contextName").textContent = filename;
        knownFiles.add(filename);
        showModeStatus("资料已上传，可以开始提问。", "success");


        if (input) input.value = "";

        showSelectedFile("fileInput", "fileSelectedName");

        loadFiles();

    } catch (err) {

        showModeStatus("上传失败：" + err.message, "error");

    } finally {

        setBtnLoading("uploadFileBtn", false);

    }

}



// =============================
// 十四、加载文件列表
// =============================

async function loadFiles() {

        const box = $("fileList");

    if (!box) return;

    try {

        const data = await apiJson("/files");

        const files = (data && data.files) ? data.files : [];

        knownFiles = new Set(files);

        box.innerHTML = '<option value="">选择已上传资料</option>';
        files.forEach(name => {
            const option = document.createElement("option");
            option.value = name;
            option.textContent = name;
            box.appendChild(option);
        });
        box.disabled = !files.length;
        if ($("fileName")?.value && knownFiles.has($("fileName").value)) {
            box.value = $("fileName").value;
            if ($("contextName")) $("contextName").textContent = $("fileName").value;
        }

    } catch (err) {

        box.innerHTML = '<option value="">资料列表暂不可用</option>';

    }

}

function selectExistingFile() {
    const picker = $("fileList");
    const filename = picker?.value || "";
    if ($("fileName")) $("fileName").value = filename;
    if ($("contextName")) $("contextName").textContent = filename;
    if ($("uploadFileBtn")) $("uploadFileBtn").hidden = true;
    if ($("fileInput")) $("fileInput").value = "";
}



// =============================
// 十五、文件问答
// =============================

async function askFile() {

    const nameEl = $("fileName");

    const qEl = $("fileQuestion");

    const filename = nameEl ? nameEl.value.trim() : "";

    const question = qEl ? qEl.value.trim() : "";


    if (!filename) {

        alert("请先输入文件名");

        return;

    }

    if (!question) {

        alert("请输入关于文件的问题");

        return;

    }



    const box = $("fileAnswer");

    const qa = document.createElement("div");

    qa.className = "qa-block";

    qa.innerHTML =
        '<div class="qa-q">🙋 ' + escapeHtml(question) + '</div>' +
        '<div class="qa-a loading">正在读取文件并思考…</div>';


    if (box) box.appendChild(qa);

    const answerEl = qa.querySelector(".qa-a");

    if (qEl) qEl.value = "";


    setBtnLoading("askFileBtn", true, "思考中…");


    try {

        const data = await apiJson(

            "/chat_file?filename=" +
            encodeURIComponent(filename) +
            "&question=" +
            encodeURIComponent(question),

            { method: "POST" }

        );


        const reply = (data && data.reply !== undefined)
            ? data.reply
            : (data._raw || "（后端未返回内容）");


        answerEl.classList.remove("loading");

        answerEl.innerHTML = renderMarkdown(reply);

    } catch (err) {

        answerEl.classList.remove("loading");

        answerEl.classList.add("error");

        answerEl.innerHTML = "❌ 回答失败：" + errorToHtml(err);

    } finally {

        setBtnLoading("askFileBtn", false);

    }

}



// =============================
// 十六、上传论文
// =============================

async function uploadPaper() {

    const input = $("paperInput");

    const file =
        (input && input.files)
            ? input.files[0]
            : null;


    if (!file) {

        showModeStatus("请先选择一篇 PDF 论文。", "error");

        return;

    }


    // 前端先做一次基本校验，省得白等
    if (!/\.pdf$/i.test(file.name)) {

        showModeStatus("文件格式不正确，请选择 PDF。", "error");

        return;

    }


    if (file.size > 50 * 1024 * 1024) {

        showModeStatus("论文文件不能超过 50 MB。", "error");

        return;

    }



    let formData = new FormData();

    formData.append("file", file);


    // 论文要切片 + 调 embedding，比较慢，先给出明确反馈
    showModeStatus("正在解析《" + file.name + "》，论文切片和向量化需要一些时间…");


    setBtnLoading("uploadPaperBtn", true, "解析中…");


    try {

        const data = await apiJson("/upload_paper", {

            method: "POST",

            body: formData

        });


        const chunks =
            (data && data.chunks !== undefined && data.chunks !== null)
                ? data.chunks
                : "-";

        const message =
            (data && data.message) ? data.message : "论文解析完成";

        const fname =
            (data && data.filename) ? data.filename : file.name;


        if ($("contextName")) $("contextName").textContent = fname;
        paperReady = true;
        if (input) input.value = "";
        if ($("uploadPaperBtn")) $("uploadPaperBtn").hidden = true;
        showModeStatus(message + " · " + fname + " · 共 " + chunks + " 个检索片段", "success");

        showBanner("");

    } catch (err) {

        showModeStatus("论文解析失败：" + err.message, "error");

    } finally {

        setBtnLoading("uploadPaperBtn", false);

    }

}



// =============================
// 十七、论文问答
// =============================

async function askPaper() {

    const input = $("paperQuestion");

    const question = input ? input.value.trim() : "";


    if (!question) {

        alert("请输入问题");

        if (input) input.focus();

        return;

    }



    const box = $("paperAnswer");


    // 把问答追加成历史记录，而不是覆盖掉上一次的答案
    const qa = document.createElement("div");

    qa.className = "qa-block";

    qa.innerHTML =
        '<div class="qa-q">🙋 ' + escapeHtml(question) + '</div>' +
        '<div class="qa-a loading">正在检索论文内容…</div>';


    if (box) {

        box.appendChild(qa);

        box.scrollTop = box.scrollHeight;

    }


    const answerEl = qa.querySelector(".qa-a");

    if (input) input.value = "";


    setBtnLoading("askPaperBtn", true, "思考中…");


    try {

        const data = await apiJson("/paper_chat", {

            method: "POST",

            headers: {

                "Content-Type": "application/json"

            },

            body: JSON.stringify({

                question: question

            })

        });


        const reply = (data && data.reply !== undefined)
            ? data.reply
            : (data._raw || "（后端未返回内容，可能还没有上传论文）");


        answerEl.classList.remove("loading");

        answerEl.innerHTML = renderMarkdown(reply);

    } catch (err) {

        answerEl.classList.remove("loading");

        answerEl.classList.add("error");

        answerEl.innerHTML = "❌ 回答失败：" + errorToHtml(err);

    } finally {

        setBtnLoading("askPaperBtn", false);

    }

}



// =============================
// 十八、音乐卡片生成
// =============================

// 判断是不是音乐卡片回复
function isMusicReply(text) {

    const str = (text === null || text === undefined) ? "" : String(text);

    return /\[\s*\/?\s*MUSIC_CARD\s*\]/i.test(str) || str.includes("【音乐卡片】");

}



// 把卡片文本解析成歌曲数组
// 原版只保留最后一首，会把一次推荐的 3 首歌压成 1 首，这里全部解析出来
function parseMusicSongs(text) {

    const songs = [];

    let current = null;

    // 把所有卡片标记统一替换成换行，避免标记粘在内容上
    const clean = String(text === null || text === undefined ? "" : text)

        .replace(/\[\s*\/?\s*MUSIC_CARD\s*\]/gi, "\n")

        .replace(/【音乐卡片】/g, "\n");

    clean.split("\n").forEach(rawLine => {

        const line = rawLine.trim();

        if (!line) return;

        const m = line.match(/^(歌曲名|歌名|歌手|演唱|理由)\s*[:：]\s*(.*)$/);

        if (!m) return;

        const key = m[1];

        const value = m[2].trim();

        if (key === "歌曲名" || key === "歌名") {

            current = { song: value, singer: "", reason: "" };

            songs.push(current);

        } else if (current) {

            if (key === "歌手" || key === "演唱") current.singer = value;

            if (key === "理由") current.reason = value;

        }

    });

    return songs;

}



function createMusicCard(text) {

    const songs = parseMusicSongs(text);

    // 一首都没解析出来时，退回普通 Markdown 渲染，避免出现空白
    if (!songs.length) {

        return renderMarkdown(text);

    }

    return songs.map(function (s) {

        const keyword = (s.singer ? s.singer + " " : "") + s.song;

        return `

<div class="music-card">

    <div class="music-title">

        🎵 ${escapeHtml(s.song || "未命名歌曲")}

    </div>

    <div class="music-singer">

        👤 ${escapeHtml(s.singer || "未知歌手")}

    </div>

    <div class="music-reason">

        💡 ${escapeHtml(s.reason)}

    </div>

    <a
        class="play-btn"
        target="_blank"
        href="https://music.163.com/#/search/m/song?keywords=${encodeURIComponent(keyword)}"
    >

        🎧 网易云播放

    </a>

</div>

`;

    }).join("\n");

}



// =============================
// 十九、后端连通性自检
// =============================

async function checkBackendHealth() {

    try {

        await apiJson("/files");

        showBanner("");
        const label = $("serviceLabel");
        if (label) label.textContent = "工作区已连接";
        $("serviceDot")?.classList.remove("offline");
        if ($("serviceDot")) $("serviceDot").style.background = "";

    } catch (err) {

        const label = $("serviceLabel");
        if (label) label.textContent = "后端连接中断";
        $("serviceDot")?.classList.add("offline");
        if ($("serviceDot")) $("serviceDot").style.background = "#d76c6c";

        let msg = "⚠️ 无法连接后端服务。<br>";

        msg += escapeHtml((err && err.message) ? err.message : String(err));

        msg += '<br><a href="debug.html" target="_blank">→ 打开接口自检页定位原因</a>';

        showBanner(msg, "error");

    }

}



// =============================
// 二十、页面初始化
// =============================

document.addEventListener(

    "DOMContentLoaded",

    function () {


        // 本地用 file:// 打开时，先给出明确提示
        if (location.protocol === "file:") {

            showBanner(

                "⚠️ 你正在用 file:// 方式直接打开网页，" +
                "浏览器不允许它访问后端接口。<br>" +
                "请把 frontend 目录用 http 服务打开（例如启动后端后通过服务器地址访问）。",

                "error"

            );

        }


        // 聊天输入框：Enter 发送
        const input = $("input");

        if (input) {

            input.addEventListener("keydown", function (e) {

                if (e.key === "Enter" && !e.shiftKey) {

                    e.preventDefault();

                    sendMessage();

                }

            });

            input.addEventListener("input", () => {
                input.style.height = "auto";
                input.style.height = Math.min(input.scrollHeight, 160) + "px";
            });

        }

        document.addEventListener("click", event => {
            const history = $("historyPanel");
            if (history && !history.hidden && !history.contains(event.target) && !$("historyToggle")?.contains(event.target)) history.hidden = true;
        });
        document.addEventListener("keydown", event => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); newChat(); }
            if (event.key === "Escape") { toggleHistory(false); if ($("modePicker")) $("modePicker").hidden = true; }
        });


        // 认证通过后才加载任何聊天、文件或论文信息。
        restoreAllowWrites();
        checkAuthentication();

    }

);

function usePrompt(text) {
    const input = $("input");
    if (!input) return;
    input.value = text;
    input.focus();
    input.dispatchEvent(new Event("input", { bubbles: true }));
}

function showLoginGate(message = "") {
    const gate = $("loginGate");
    if (gate) gate.hidden = false;
    const error = $("loginError");
    if (error) error.textContent = message;
    if (!message) setTimeout(() => $("loginPassword")?.focus(), 50);
}

function enterWorkspace() {
    if ($("loginGate")) $("loginGate").hidden = true;
    if ($("loginPassword")) $("loginPassword").value = "";
    if ($("loginError")) $("loginError").textContent = "";
    loadChats();
    loadFiles();
    checkBackendHealth();
    refreshModelPill();
}

async function checkAuthentication() {
    try {
        const data = await apiJson("/auth/status");
        if (data && data.authenticated) enterWorkspace();
        else showLoginGate();
    } catch (error) {
        showLoginGate("暂时无法连接服务：" + error.message);
    }
}

async function submitLogin(event) {
    event.preventDefault();
    const input = $("loginPassword");
    const button = $("loginBtn");
    const password = input?.value || "";
    if (!password) return;
    if (button) button.disabled = true;
    if ($("loginError")) $("loginError").textContent = "";
    try {
        await apiJson("/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password })
        });
        enterWorkspace();
    } catch (error) {
        showLoginGate(error.message || "登录失败，请检查密码。 ");
        input?.select();
    } finally {
        if (button) button.disabled = false;
    }
}

async function logout() {
    try { await apiJson("/auth/logout", { method: "POST" }); } catch (_) { /* Session may already have expired. */ }
    currentChatId = null;
    clearChat();
    showLoginGate();
}

// =============================
// 学习档案的写入开关
// =============================
// 默认关闭：助手不能写入长期记忆，也不能勾选任务。
// 打开后，后端才会注册 remember / set_task_status 三个写入工具，
// 而且仍要求「用户明确要求」才调用。状态存在本地，刷新后保持。

function onAllowWritesChange() {
    const box = $("allowWrites");
    allowWrites = Boolean(box && box.checked);
    box?.closest(".write-toggle")?.classList.toggle("on", allowWrites);
    try { localStorage.setItem("allowWrites", allowWrites ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }
}

// 工作区只读开关：开启后后端才注册 list_dir / read_file / grep_code
function onAllowWorkspaceChange() {
    const box = $("allowWorkspace");
    allowWorkspace = Boolean(box && box.checked);
    box?.closest(".write-toggle")?.classList.toggle("on", allowWorkspace);
    try { localStorage.setItem("allowWorkspace", allowWorkspace ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }
}

// 改代码开关：开启后后端才注册 write_file / edit_file。
// 改代码必然要先读代码，所以勾上它时顺手把「允许读代码」也打开，
// 避免出现「能写不能读」这种做不了事的状态。
function onAllowCodeEditChange() {
    const box = $("allowCodeEdit");
    allowCodeEdit = Boolean(box && box.checked);
    box?.closest(".write-toggle")?.classList.toggle("on", allowCodeEdit);
    try { localStorage.setItem("allowCodeEdit", allowCodeEdit ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }

    if (allowCodeEdit && !allowWorkspace) {
        const wsBox = $("allowWorkspace");
        if (wsBox) {
            wsBox.checked = true;
            onAllowWorkspaceChange();
        }
    }
}

// 命令执行开关：开启后后端才注册 run_command。
// 需要确认的命令仍会弹卡片，勾选这个只代表「允许它申请执行」，不代表可以随便跑。
function onAllowCommandChange() {
    const box = $("allowCommand");
    allowCommand = Boolean(box && box.checked);
    box?.closest(".write-toggle")?.classList.toggle("on", allowCommand);
    try { localStorage.setItem("allowCommand", allowCommand ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }
}

// 思考过程开关：开启后请求模型返回 reasoning_content，显示在过程面板里
function onShowThinkingChange() {
    const box = $("showThinking");
    showThinking = Boolean(box && box.checked);
    box?.closest(".write-toggle")?.classList.toggle("on", showThinking);
    try { localStorage.setItem("showThinking", showThinking ? "1" : "0"); } catch (_) { /* 隐私模式忽略 */ }
}

function restoreAllowWrites() {
    const box = $("allowWrites");
    if (!box) return;
    try { box.checked = localStorage.getItem("allowWrites") === "1"; } catch (_) { box.checked = false; }
    allowWrites = Boolean(box.checked);
    box.closest(".write-toggle")?.classList.toggle("on", allowWrites);

    const wsBox = $("allowWorkspace");
    if (!wsBox) return;
    try { wsBox.checked = localStorage.getItem("allowWorkspace") === "1"; } catch (_) { wsBox.checked = false; }
    allowWorkspace = Boolean(wsBox.checked);
    wsBox.closest(".write-toggle")?.classList.toggle("on", allowWorkspace);

    const thinkBox = $("showThinking");
    if (!thinkBox) return;
    try { thinkBox.checked = localStorage.getItem("showThinking") === "1"; } catch (_) { thinkBox.checked = false; }
    showThinking = Boolean(thinkBox.checked);
    thinkBox.closest(".write-toggle")?.classList.toggle("on", showThinking);

    const codeBox = $("allowCodeEdit");
    if (!codeBox) return;
    try { codeBox.checked = localStorage.getItem("allowCodeEdit") === "1"; } catch (_) { codeBox.checked = false; }
    allowCodeEdit = Boolean(codeBox.checked);
    codeBox.closest(".write-toggle")?.classList.toggle("on", allowCodeEdit);
    // 恢复时不开「读代码」：只要用户自己勾过写，读由后端隐式带上，界面不改用户的勾选

    const cmdBox = $("allowCommand");
    if (!cmdBox) return;
    try { cmdBox.checked = localStorage.getItem("allowCommand") === "1"; } catch (_) { cmdBox.checked = false; }
    allowCommand = Boolean(cmdBox.checked);
    cmdBox.closest(".write-toggle")?.classList.toggle("on", allowCommand);
}

async function openLearningHub() {
    const dialog = $("learningHub");
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    await Promise.allSettled([loadMemories(), loadLearningPlans()]);
}

function closeLearningHub() {
    const dialog = $("learningHub");
    if (dialog?.open) dialog.close();
}

async function loadMemories() {
    const box = $("memoryList");
    if (!box) return;
    try {
        const data = await apiJson("/memories");
        box.replaceChildren();
        const items = data.items || [];
        if (!items.length) {
            box.innerHTML = '<div class="empty-hint">还没有保存的记忆。</div>';
            return;
        }
        items.forEach(item => {
            const card = document.createElement("article");
            card.className = "hub-item";
            const category = document.createElement("span");
            category.className = "hub-category";
            category.textContent = item.category || "记忆";
            // 助手写入的记忆单独标出来，方便你审计和删除
            if (item.source === "agent") {
                const badge = document.createElement("span");
                badge.className = "hub-source";
                badge.textContent = "助手写入";
                badge.title = "这是助手根据你的要求写入的，可随时删除";
                category.after(badge);
            }
            const content = document.createElement("p");
            content.textContent = item.content;
            const remove = document.createElement("button");
            remove.className = "hub-remove";
            remove.textContent = "删除";
            remove.onclick = async () => {
                try {
                    await apiJson("/memories/" + item.id, { method: "DELETE" });
                    loadMemories();
                } catch (error) { alert("删除记忆失败：" + error.message); }
            };
            card.append(category, remove, content);
            box.appendChild(card);
        });
    } catch (error) {
        box.textContent = "读取记忆失败：" + error.message;
    }
}

async function saveLearningMemory(event) {
    event.preventDefault();
    const content = $("memoryContent")?.value.trim() || "";
    const category = $("memoryCategory")?.value.trim() || "偏好";
    if (!content) return;
    try {
        await apiJson("/memories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content, category })
        });
        $("memoryContent").value = "";
        await loadMemories();
    } catch (error) {
        alert("保存记忆失败：" + error.message);
    }
}

async function loadLearningPlans() {
    const box = $("planList");
    if (!box) return;
    try {
        const data = await apiJson("/learning_plans");
        box.replaceChildren();
        const items = data.items || [];
        if (!items.length) {
            box.innerHTML = '<div class="empty-hint">还没有学习计划。</div>';
            return;
        }
        items.forEach(plan => {
            const card = document.createElement("details");
            card.className = "hub-item plan-item";
            const summary = document.createElement("summary");
            const completed = Number(plan.completed_tasks || 0);
            const total = Number(plan.total_tasks || 0);
            summary.textContent = (plan.status === "done" ? "✓ " : "◷ ") + plan.title + (total ? ` · ${completed}/${total}` : "");
            const goal = document.createElement("p");
            goal.className = "learning-help";
            goal.textContent = "目标：" + plan.goal;
            const progress = document.createElement("div");
            progress.className = "plan-progress";
            const progressFill = document.createElement("span");
            progressFill.style.width = total ? `${Math.round(completed / total * 100)}%` : "0%";
            progress.appendChild(progressFill);
            const content = document.createElement("div");
            content.className = "plan-content";
            content.textContent = plan.content;
            const tasks = document.createElement("div");
            tasks.className = "task-list";
            (plan.tasks || []).forEach(task => {
                const item = document.createElement("div");
                item.className = "task-item" + (task.status === "done" ? " done" : "");
                const check = document.createElement("input");
                check.type = "checkbox";
                check.checked = task.status === "done";
                check.setAttribute("aria-label", "标记任务完成");
                check.onchange = async () => {
                    check.disabled = true;
                    try {
                        await apiJson(`/learning_plans/${plan.id}/tasks/${task.id}`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ status: check.checked ? "done" : "todo" })
                        });
                        loadLearningPlans();
                    } catch (error) {
                        check.checked = !check.checked;
                        alert("更新任务失败：" + error.message);
                    } finally { check.disabled = false; }
                };
                const body = document.createElement("div");
                const title = document.createElement("strong");
                title.textContent = task.title;
                const meta = document.createElement("small");
                meta.textContent = [task.stage, task.details].filter(Boolean).join(" · ");
                body.append(title);
                if (meta.textContent) body.appendChild(meta);
                const removeTask = document.createElement("button");
                removeTask.className = "task-remove";
                removeTask.textContent = "×";
                removeTask.title = "删除任务";
                removeTask.onclick = async () => {
                    try {
                        await apiJson(`/learning_plans/${plan.id}/tasks/${task.id}`, { method: "DELETE" });
                        loadLearningPlans();
                    } catch (error) { alert("删除任务失败：" + error.message); }
                };
                item.append(check, body, removeTask);
                tasks.appendChild(item);
            });
            if (!plan.tasks?.length) {
                tasks.innerHTML = '<div class="empty-hint">这是旧版计划，没有可勾选任务。点击“重新规划”即可生成待办。</div>';
            }
            const taskForm = document.createElement("form");
            taskForm.className = "task-form";
            const taskTitle = document.createElement("input");
            taskTitle.placeholder = "添加一项任务，例如：阅读注意力机制章节";
            taskTitle.maxLength = 180;
            const taskStage = document.createElement("input");
            taskStage.placeholder = "阶段，例如：第 2 天";
            taskStage.maxLength = 80;
            const addTask = document.createElement("button");
            addTask.type = "submit";
            addTask.textContent = "添加任务";
            taskForm.append(taskTitle, taskStage, addTask);
            taskForm.onsubmit = async event => {
                event.preventDefault();
                const titleValue = taskTitle.value.trim();
                if (!titleValue) return;
                addTask.disabled = true;
                try {
                    await apiJson(`/learning_plans/${plan.id}/tasks`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ title: titleValue, stage: taskStage.value.trim() })
                    });
                    loadLearningPlans();
                } catch (error) { alert("添加任务失败：" + error.message); }
                finally { addTask.disabled = false; }
            };
            const adjustBox = document.createElement("div");
            adjustBox.className = "plan-adjust";
            const adjustNote = document.createElement("input");
            adjustNote.placeholder = "例如：这周只剩两天，帮我压缩计划";
            adjustNote.maxLength = 1000;
            const adjustButton = document.createElement("button");
            adjustButton.textContent = "重新规划";
            adjustButton.onclick = async () => {
                adjustButton.disabled = true;
                adjustButton.textContent = "正在调整…";
                try {
                    await apiJson(`/learning_plans/${plan.id}/adjust`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ note: adjustNote.value.trim() })
                    });
                    loadLearningPlans();
                } catch (error) { alert("调整计划失败：" + error.message); }
                finally { adjustButton.disabled = false; adjustButton.textContent = "重新规划"; }
            };
            adjustBox.append(adjustNote, adjustButton);
            const actions = document.createElement("div");
            actions.className = "hub-item-actions";
            const toggle = document.createElement("button");
            toggle.textContent = plan.status === "done" ? "设为进行中" : "标记完成";
            toggle.onclick = async () => {
                const status = plan.status === "done" ? "active" : "done";
                try {
                    await apiJson(`/learning_plans/${plan.id}?status=${status}`, { method: "PATCH" });
                    loadLearningPlans();
                } catch (error) { alert("更新计划失败：" + error.message); }
            };
            const remove = document.createElement("button");
            remove.className = "hub-remove";
            remove.textContent = "删除";
            remove.onclick = async () => {
                try {
                    await apiJson("/learning_plans/" + plan.id, { method: "DELETE" });
                    loadLearningPlans();
                } catch (error) { alert("删除计划失败：" + error.message); }
            };
            actions.append(toggle, remove);
            card.append(summary, goal, progress, content, tasks, taskForm, adjustBox, actions);
            box.appendChild(card);
        });
    } catch (error) {
        box.textContent = "读取学习计划失败：" + error.message;
    }
}

async function generateLearningPlan(event) {
    event.preventDefault();
    const goal = $("planGoal")?.value.trim() || "";
    const button = $("generatePlanBtn");
    if (!goal || !button) return;
    button.disabled = true;
    button.textContent = "正在生成…";
    try {
        await apiJson("/learning_plans/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ goal })
        });
        $("planGoal").value = "";
        await loadLearningPlans();
    } catch (error) {
        alert("生成计划失败：" + error.message);
    } finally {
        button.disabled = false;
        button.textContent = "生成任务计划";
    }
}

// =============================
// 二十一、模型与 API Key
// =============================
// 服务商配置存在后端 SQLite，前端只拿到掩码后的 Key。
// 切换后不用刷新页面：后端每次调用都会重新读取当前激活项。

let providers = [];
let providerPresets = [];

function currentPreset() {
    const select = $("providerPreset");
    return providerPresets.find(item => item.id === (select?.value || "")) || null;
}

function setProviderStatus(message, type = "") {
    const box = $("providerStatus");
    if (!box) return;
    if (!message) {
        box.hidden = true;
        box.textContent = "";
        box.className = "provider-status";
        return;
    }
    box.hidden = false;
    box.className = "provider-status" + (type ? " " + type : "");
    box.textContent = message;
}

function updateModelPill(active) {
    const pill = $("modelPill");
    const label = $("modelPillText");
    if (!label) return;
    if (!active) {
        label.textContent = "配置模型";
        pill?.classList.add("warn");
        if (pill) pill.title = "还没有配置可用的 API Key，点击添加";
        return;
    }
    pill?.classList.remove("warn");
    label.textContent = active.model || active.name;
    if (pill) pill.title = `${active.name} · ${active.model}\n点击切换模型或修改 API Key`;
}

async function refreshModelPill() {
    try {
        const data = await apiJson("/models");
        updateModelPill(data.active);
    } catch (_) {
        // 顶栏提示失败不影响任何功能，等用户打开面板时再报错
    }
}

function renderPresetOptions() {
    const select = $("providerPreset");
    if (!select) return;
    const keep = select.value;
    select.replaceChildren();
    providerPresets.forEach(item => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.name;
        select.appendChild(option);
    });
    if (keep && providerPresets.some(item => item.id === keep)) select.value = keep;
    else if (providerPresets.length) select.value = providerPresets[0].id;
}

function applyProviderPreset() {
    const preset = currentPreset();
    if (!preset) return;
    if ($("providerHint")) $("providerHint").textContent = preset.hint || "";
    const nameInput = $("providerName");
    // 只在名字为空、或还是别的预设默认名时才覆盖，避免冲掉用户自己的命名
    if (nameInput && (!nameInput.value.trim() || providerPresets.some(item => item.name === nameInput.value.trim()))) {
        nameInput.value = preset.name;
    }
    if ($("providerBaseUrl")) $("providerBaseUrl").value = preset.base_url || "";
    if ($("providerModel")) $("providerModel").value = preset.model || "";
    if ($("providerVisionModel")) $("providerVisionModel").value = preset.vision_model || "";
}

function resetProviderForm() {
    if ($("providerId")) $("providerId").value = "";
    if ($("providerName")) $("providerName").value = "";
    if ($("providerBaseUrl")) $("providerBaseUrl").value = "";
    if ($("providerModel")) $("providerModel").value = "";
    if ($("providerVisionModel")) $("providerVisionModel").value = "";
    if ($("providerKey")) {
        $("providerKey").value = "";
        $("providerKey").placeholder = "API Key";
    }
    if ($("providerSaveBtn")) $("providerSaveBtn").textContent = "保存服务商";
    const select = $("providerPreset");
    if (select && providerPresets.length) select.value = providerPresets[0].id;
    applyProviderPreset();
    setProviderStatus("");
}

function editProvider(id) {
    const item = providers.find(entry => entry.id === id);
    if (!item) return;
    if ($("providerId")) $("providerId").value = String(item.id);
    const select = $("providerPreset");
    if (select) select.value = item.provider || "custom";
    if ($("providerName")) $("providerName").value = item.name || "";
    if ($("providerBaseUrl")) $("providerBaseUrl").value = item.base_url || "";
    if ($("providerModel")) $("providerModel").value = item.model || "";
    if ($("providerVisionModel")) $("providerVisionModel").value = item.vision_model || "";
    if ($("providerKey")) {
        $("providerKey").value = "";
        $("providerKey").placeholder = item.key_hint
            ? "留空表示不修改当前 Key（" + item.key_hint + "）"
            : "API Key";
    }
    if ($("providerSaveBtn")) $("providerSaveBtn").textContent = "保存修改";
    if ($("providerHint")) {
        const preset = providerPresets.find(entry => entry.id === item.provider);
        $("providerHint").textContent = preset?.hint || "留空的地方表示保持不变。";
    }
    setProviderStatus("");
}

function renderProviderList() {
    const box = $("providerList");
    if (!box) return;
    box.replaceChildren();
    if (!providers.length) {
        box.innerHTML = '<div class="empty-hint">还没有配置。右侧选一个预设，填上 Key 保存即可。</div>';
        return;
    }
    providers.forEach(item => {
        const card = document.createElement("article");
        card.className = "hub-item provider-item" + (item.is_active ? " active" : "");

        const title = document.createElement("div");
        title.className = "provider-title";
        const name = document.createElement("strong");
        name.textContent = item.name;
        title.appendChild(name);
        if (item.is_active) {
            const badge = document.createElement("span");
            badge.className = "provider-badge";
            badge.textContent = "使用中";
            title.appendChild(badge);
        }

        const meta = document.createElement("p");
        meta.className = "provider-meta";
        meta.textContent = [
            item.model,
            item.vision_model ? "视觉 " + item.vision_model : "",
            item.key_hint || "未填 Key",
        ].filter(Boolean).join(" · ");

        const actions = document.createElement("div");
        actions.className = "hub-item-actions";

        if (!item.is_active) {
            const use = document.createElement("button");
            use.type = "button";
            use.textContent = "使用";
            use.addEventListener("click", () => activateProvider(item.id));
            actions.appendChild(use);
        }

        const test = document.createElement("button");
        test.type = "button";
        test.textContent = "测试";
        test.addEventListener("click", () => testProviderSaved(item.id));
        actions.appendChild(test);

        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "编辑";
        edit.addEventListener("click", () => editProvider(item.id));
        actions.appendChild(edit);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "danger";
        remove.textContent = "删除";
        remove.addEventListener("click", () => deleteProvider(item.id));
        actions.appendChild(remove);

        card.append(title, meta, actions);
        box.appendChild(card);
    });
}

async function loadProviders() {
    try {
        const data = await apiJson("/models");
        providers = data.providers || [];
        providerPresets = data.presets || [];
        renderPresetOptions();
        renderProviderList();
        updateModelPill(data.active);
        return data;
    } catch (error) {
        const box = $("providerList");
        if (box) box.innerHTML = '<div class="empty-hint">加载失败：' + escapeHtml(error.message) + "</div>";
        return null;
    }
}

async function openModelSettings() {
    const dialog = $("modelSettings");
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    const data = await loadProviders();
    resetProviderForm();
    // loadProviders 之后再 reset：先把预设列表填好，reset 才能选中第一项
    return data;
}

function closeModelSettings() {
    const dialog = $("modelSettings");
    if (dialog?.open) dialog.close();
}

async function saveProvider(event) {
    event.preventDefault();
    const button = $("providerSaveBtn");
    const id = $("providerId")?.value || "";
    const preset = currentPreset();
    const payload = {
        provider: preset ? preset.id : "custom",
        name: ($("providerName")?.value || "").trim() || (preset ? preset.name : "自定义"),
        base_url: ($("providerBaseUrl")?.value || "").trim(),
        api_key: ($("providerKey")?.value || "").trim(),
        model: ($("providerModel")?.value || "").trim(),
        vision_model: ($("providerVisionModel")?.value || "").trim()
    };

    if (!payload.base_url || !payload.model) {
        setProviderStatus("接口地址和模型名都要填。", "error");
        return;
    }
    if (!id && !payload.api_key) {
        setProviderStatus("新增服务商必须填写 API Key。", "error");
        return;
    }

    if (button) {
        button.disabled = true;
        button.textContent = "保存中…";
    }
    try {
        const data = await apiJson(id ? "/models/" + id : "/models", {
            method: id ? "PATCH" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        await loadProviders();
        resetProviderForm();
        if (data && data.provider) {
            setProviderStatus("已保存：" + data.provider.name + " · " + data.provider.model, "ok");
        }
    } catch (error) {
        setProviderStatus(error.message || "保存失败", "error");
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = $("providerId")?.value ? "保存修改" : "保存服务商";
        }
    }
}

async function runProviderTest(payload) {
    setProviderStatus("正在测试连接…");
    try {
        const data = await apiJson("/models/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (data.ok) {
            setProviderStatus(data.message || "连接正常", "ok");
        } else {
            setProviderStatus(data.message || "连接失败", "error");
        }
    } catch (error) {
        setProviderStatus(error.message || "连接失败", "error");
    }
}

function testProviderDraft() {
    const id = $("providerId")?.value || "";
    const model = ($("providerModel")?.value || "").trim();
    if (!model) {
        setProviderStatus("先填模型名再测试。", "error");
        return;
    }
    runProviderTest({
        provider_id: id ? Number(id) : null,
        base_url: ($("providerBaseUrl")?.value || "").trim(),
        api_key: ($("providerKey")?.value || "").trim(),
        model
    });
}

async function testProviderSaved(id) {
    const item = providers.find(entry => entry.id === id);
    if (!item) return;
    await openModelSettings();
    editProvider(id);
    runProviderTest({ provider_id: id, base_url: item.base_url, api_key: "", model: item.model });
}

async function activateProvider(id) {
    try {
        const data = await apiJson("/models/" + id + "/activate", { method: "POST" });
        updateModelPill(data.active);
        await loadProviders();
        setProviderStatus("已切换到 " + ((data.active && data.active.model) || "新模型"), "ok");
    } catch (error) {
        setProviderStatus("切换失败：" + (error.message || "未知错误"), "error");
    }
}

async function deleteProvider(id) {
    const item = providers.find(entry => entry.id === id);
    if (!confirm("删除「" + ((item && item.name) || "该服务商") + "」？已保存的 Key 会一起删除。")) return;
    try {
        const data = await apiJson("/models/" + id, { method: "DELETE" });
        if (data.active) updateModelPill(data.active);
        await loadProviders();
        setProviderStatus("已删除", "ok");
    } catch (error) {
        setProviderStatus("删除失败：" + (error.message || "未知错误"), "error");
    }
}

