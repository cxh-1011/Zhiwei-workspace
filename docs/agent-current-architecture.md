# 知微 Agent 当前实现说明

## 对话与工具

- `POST /chat` 是统一聊天入口，接受 `mode`：`chat`、`file`、`paper`。
- 千问通过 DashScope OpenAI 兼容接口 Function Calling 选择工具。
- 工具都在 `backend/tools/` 注册：`web_search`、`scholarly_search`、`paper_search`、`search_uploaded_files`、`list_uploaded_files`。
- `scholarly_search` 实时查询 Semantic Scholar 与 Crossref 的学术元数据，返回题名、作者、年份、摘要、DOI 和可用的开放获取链接。它不登录或抓取知网、IEEE Xplore、ScienceDirect，也不绕过付费全文权限。可选配置 `SEMANTIC_SCHOLAR_API_KEY` 提高 Semantic Scholar API 使用能力，`CROSSREF_MAILTO` 用于 Crossref polite pool。
- 工具只做搜索和检索，没有任意 Python、Shell 或文件写入工具。
- 工具结果会回给千问；它可以继续调用工具，或生成最终回答。
- 文件问答支持 TXT、PDF、DOCX，以及 JPG、JPEG、PNG、WEBP 图片。图片（最大 10 MB）作为 Base64 视觉输入发送给配置的 `AGENT_VISION_MODEL`，默认 `qwen3-vl-plus`；图像问答不走文本切片检索。
- 默认每次最多 5 轮、每轮 3 个工具、60 秒、12,000 tokens；环境变量可以调节，代码另有硬上限。
- 聊天历史保留最近 12 条供短期上下文使用。论文检索仍使用当前 FAISS 论文索引；重新上传论文会替换当前索引。

## 运行记录

- `agent_runs` 保存一次执行的状态、用时和回答。
- `agent_steps` 保存模型轮次、工具名称、参数、结果摘要和成功状态。
- `GET /agent_runs?chat_id=...` 返回会话运行记录，历史聊天打开后也会显示对应工具过程。

## 长期记忆与学习计划

- 顶部“学习档案”入口允许用户主动保存或删除长期记忆。
- Agent 只参考用户确认保存的记忆和进行中的计划，不会自动把聊天内容写入长期记忆。
- 学习计划由用户填写目标后调用千问生成并保存；可以标记完成、恢复或删除。
- 记忆最多保留 100 条，计划最多保留最近 200 条。

## 部署

部署前先备份服务器上的代码、`.env`、`chats.db`、`uploads/` 和 `paper_index/`。新版本新增 `openai` Python 依赖，上传代码后需要在 Python 3.11 虚拟环境里执行：

```bash
python -m pip install -r requirements.txt
```

随后重启 Uvicorn，并确认 `/auth/status`、`/docs` 和登录后的聊天页面可用。这个工作区只完成本地代码，尚未连接或修改阿里云服务器。
