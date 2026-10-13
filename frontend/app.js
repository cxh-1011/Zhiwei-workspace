// =============================
// AI机器人前端 JS
// 阿里云部署版（前端健壮性加固版 v2）
// =============================


// 当前聊天ID
let currentChatId = null;
let chatCreation = null;
let knownFiles = new Set();
let paperReady = false;

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
                            bubble.appendChild(renderAgentTrace(run.steps, run.status, run.rounds, run.duration_ms));
                        }
                        const answer = document.createElement("div");
                        answer.className = "agent-answer";
                        answer.innerHTML = renderMarkdown(msg[3]);
                        bubble.appendChild(answer);
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

async function sendMessage() {

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


    // 显示用户消息
    addMessage("user", text);

    input.value = "";
    input.style.height = "auto";


    // AI思考动画
    const thinking = document.createElement("div");

    thinking.className = "ai thinking";

    thinking.innerHTML = `
        <span></span><span></span><span></span>
        <small>正在分析问题，并判断是否需要检索资料…</small>
    `;

    const chat = $("chat");

    if (!chat) return;

    chat.appendChild(thinking);

    chat.scrollTop = chat.scrollHeight;

    setBtnLoading("sendBtn", true, "发送中…");


    try {

        // 没有当前会话就先建一个
        const chatId = await ensureChatId();

        const data = await apiJson("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text: text,
                chat_id: chatId,
                mode: activeMode || "chat",
                filename: activeMode === "file" ? selectedFilename : null
            })
        });

        thinking.remove();

        const reply = (data && data.reply !== undefined)
            ? data.reply
            : "（后端未返回内容）";

        const titleEl = $("currentTitle");
        if (titleEl) titleEl.textContent = text.slice(0, 15);
        loadChats();

        const div = document.createElement("div");

        div.className = "ai";

        chat.appendChild(div);

        if (Array.isArray(data.steps) && data.steps.length) {
            div.appendChild(renderAgentTrace(data.steps, data.status, data.rounds, data.duration_ms));
        }

        const answer = document.createElement("div");
        answer.className = "agent-answer";
        div.appendChild(answer);

        // 判断音乐卡片
        if (activeMode === "chat" && isMusicReply(reply)) {

            answer.innerHTML = createMusicCard(reply);

        } else {

            // 普通AI回答逐字显示
            typeWriter(answer, reply, 30);

        }

        chat.scrollTop = chat.scrollHeight;

    } catch (err) {

        thinking.remove();

        addMessage("ai", "请求失败：" + err.message);

    } finally {

        setBtnLoading("sendBtn", false);

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

function renderAgentTrace(steps, status, rounds, durationMs) {
    const details = document.createElement("details");
    details.className = "agent-trace";
    const summary = document.createElement("summary");
    const tools = steps.filter(step => step.kind === "tool_call");
    const headline = tools.length
        ? `已调用 ${tools.length} 个工具 · ${rounds || 1} 轮 · ${((durationMs || 0) / 1000).toFixed(1)} 秒`
        : `分析完成 · ${((durationMs || 0) / 1000).toFixed(1)} 秒`;
    summary.textContent = "⚙　" + headline;
    details.appendChild(summary);
    const list = document.createElement("ol");
    list.className = "agent-trace-list";
    const labels = {
        web_search: "联网搜索",
        paper_search: "检索论文",
        search_uploaded_files: "检索上传资料",
        list_uploaded_files: "查看资料列表"
    };
    steps.forEach(step => {
        const item = document.createElement("li");
        const title = document.createElement("strong");
        if (step.kind === "tool_call") {
            title.textContent = (step.ok ? "✓ " : "! ") + (labels[step.tool] || step.tool);
            item.className = step.ok ? "trace-ok" : "trace-error";
            const query = step.args && (step.args.query || step.args.question);
            if (query) {
                const queryLine = document.createElement("div");
                queryLine.className = "trace-detail";
                queryLine.textContent = "检索内容：" + query;
                item.appendChild(title);
                item.appendChild(queryLine);
            } else item.appendChild(title);
            if (step.elapsed_ms != null) {
                const time = document.createElement("span");
                time.className = "trace-time";
                time.textContent = `${step.elapsed_ms} ms`;
                item.appendChild(time);
            }
            if (step.result_preview) {
                const preview = document.createElement("p");
                preview.className = "trace-preview";
                preview.textContent = step.result_preview;
                item.appendChild(preview);
            }
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
    details.appendChild(list);
    if (status && status !== "ok") {
        const notice = document.createElement("div");
        notice.className = "trace-status";
        notice.textContent = status === "max_rounds" ? "已达到最大工具调用轮数，回答可能不完整。" :
            status === "timeout" ? "本次运行达到时间上限，回答基于已有资料。" : "本次运行未能完整完成。";
        details.appendChild(notice);
    }
    return details;
}



// =============================
// 十二、清空聊天
// =============================

function clearChat() {

    const box = $("chat");

    if (box) {

        box.innerHTML = "";

        if ($("currentTitle")) $("currentTitle").textContent = "新对话";

        box.innerHTML = '<div id="welcome" class="welcome"><div class="welcome-orb"><i></i><i></i><i></i></div><div class="eyebrow">YOUR THINKING PARTNER</div><h1>你好，今天想用 AI 做什么？</h1><p>先选择一个功能，接下来的提问和回答都会在这里进行。</p><div class="mode-grid"><button class="mode-card" onclick="selectMode(\'chat\')"><span class="mode-icon blue">✳</span><span><b>智能对话</b><small>自由提问、灵感探索与音乐推荐</small></span><span class="mode-arrow">↗</span></button><button class="mode-card" onclick="selectMode(\'file\')"><span class="mode-icon purple">▤</span><span><b>文件问答</b><small>上传 PDF、DOCX 或 TXT，基于资料提问</small></span><span class="mode-arrow">↗</span></button><button class="mode-card" onclick="selectMode(\'paper\')"><span class="mode-icon amber">论</span><span><b>论文助手</b><small>解析 PDF 论文，检索内容并深入讨论</small></span><span class="mode-arrow">↗</span></button></div></div>';
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
    file: { name: "文件问答", icon: "▤", placeholder: "针对这份资料提问…", hint: "回答将根据所选资料生成", greeting: "请先添加或选择一份资料，然后告诉我你想了解什么。" },
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
        showModeStatus("论文解析完成 · " + fname + " · 共 " + chunks + " 个检索片段", "success");

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
            summary.textContent = (plan.status === "done" ? "✓ " : "◷ ") + plan.title;
            const goal = document.createElement("p");
            goal.className = "learning-help";
            goal.textContent = "目标：" + plan.goal;
            const content = document.createElement("div");
            content.className = "plan-content";
            content.textContent = plan.content;
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
            card.append(summary, goal, content, actions);
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
        button.textContent = "生成并保存计划";
    }
}

