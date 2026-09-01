# Local TTS Workbench V1.1

一个单用户、本机运行的 Fish Audio TTS 工作台。浏览器提供中文 UI，SQLite 保存角色、项目和生成历史，音频与 metadata 保存到本地文件系统。它不会开放公网，也不会自动重试可能产生费用的请求。

## 安装与启动

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。在项目目录运行：

```bash
uv sync
uv run python -m app.main
```

然后打开 <http://127.0.0.1:8000>。可选 `uv run python -m app.main --open` 自动打开浏览器。服务默认只监听 `127.0.0.1`。

Windows 可直接双击桌面的 **Local TTS Workbench** 快捷方式启动并打开浏览器。使用完毕后点击页面顶部的“退出程序”，服务会完成数据库清理并正常结束。

## Fish API Key

在“设置”页输入 API Key。Key 只写入项目根目录 `.env` 的 `FISH_API_KEY`，写入时保留 `.env` 的其他内容，并立即更新当前进程；完整 Key 不会返回 HTML/JSON，也不会写入 SQLite、metadata 或应用日志。

“测试连接”调用官方无费用接口 `GET https://api.fish.audio/wallet/self/api-credit`，不会生成 TTS。

也可手工复制 `.env.example` 为 `.env` 后填写：

```dotenv
FISH_API_KEY=your_key_here
```

## 日常使用

1. 在“项目”创建显示名称与安全 slug，例如“第一章” / `chapter_01`。
2. 在“角色”绑定已有 Fish `reference_id`，或上传 1–20 个你有权使用的参考音频。Voice Clone 使用 `POST /model`，明确发送 `type=tts`、`train_mode=fast`、`visibility=private`、`generate_sample=false`。克隆失败会保留角色和本地音频，可手动重试。
3. 在“生成”选择项目和角色，填写正文与情绪描述。S2 系列将情绪描述组合为 `[自然语言描述]\n正文`；正文内已有的 `[sigh]` 等局部 cue 原样保留。S1 使用其官方圆括号语法。
4. 页面实时预览最终 Fish 输入、UTF-8 bytes 与预计费用；后端会独立重算。生成成功后无需刷新即可试听。
5. “历史”可按角色过滤、试听、复制参数、重新生成或删除。重新生成会建立新记录与新编号，不覆盖原音频。

## 文件与数据

- 数据库：`data/app.db`
- 角色参考音频：`data/characters/{character_id}/`
- 项目音频：`data/projects/{slug}/audio/001_角色名.mp3`
- metadata：`data/projects/{slug}/metadata/001_角色名.json`

metadata 与音频同名但分目录保存，不包含 API Key。音频只能通过数据库 generation id 的受控 endpoint 访问，`data/` 未作为静态目录暴露。

项目编号由 SQLite 中的 `project.next_sequence` 单调递增分配。明确失败不占用编号；编号一旦分配便不回收，因此本地落盘故障时可能出现安全的编号缺口。删除历史不会降低计数器。

## 费用估算

公式：

```text
utf8_bytes = len(final_text.encode("utf-8"))
预计费用 = utf8_bytes / 1,000,000 × 用户设置的单价
```

默认单价为 15 USD / 1M UTF-8 bytes，来源是 Fish 官方 Pricing & Rate Limits。`s2.1-pro-free` 的官方单价是 0；为避免模型切换时悄悄改变用户设置，V1 的价格仍是一个全局、可修改的预计单价，选择 free 档时如需 `$0` 估算请在设置中改为 0。

## Fish API 接入（核对日期：2026-09-01）

Fish API integration last verified: **2026-09-01**

- 生产推荐模型：`s2.1-pro`；免费开发模型：`s2.1-pro-free`；仍支持 `s2-pro`、`s1`
- TTS：`POST https://api.fish.audio/v1/tts`
- 认证：`Authorization: Bearer ...`
- 模型：`model` 请求头
- 主体字段：`text`、`reference_id`、`format`；非空语速发送为 `prosody.speed`
- 输出：binary audio；支持 `mp3`、`wav`、`pcm`、`opus`
- Voice Clone：multipart `POST https://api.fish.audio/model`，1–20 个 `voices` 文件，强制 `visibility=private`
- S2.1/S2 自然语言情绪：方括号 cue，可出现在文本任意位置
- 当前官方定价：`s2.1-pro` / `s2-pro` / `s1` 为 $15/M UTF-8 bytes；`s2.1-pro-free` 为 $0/M（fair-use，且无 TTFA/DPA 保证）

官方资料：[TTS API](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)、[Create Model API](https://docs.fish.audio/api-reference/endpoint/model/create-model)、[Models Overview](https://docs.fish.audio/developer-guide/models-pricing/models-overview)、[Pricing](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits)。

## 失败语义与防重复扣费

- 每次生成使用数据库唯一的 `client_request_id`；同一 id 的重复 POST 返回已有记录，不再次请求 Fish。
- 前端提交后立即禁用按钮。
- TTS 和 Voice Clone POST 均没有自动 retry；429、5xx 和 timeout 都交给用户决定是否重试。
- 连接建立前失败或明确 HTTP 错误记为 `failed`。
- 请求发送后 read/write timeout 或连接中断记为 `uncertain`，明确提示 Fish 可能已经处理。
- Fish 成功响应先写临时文件、验证音频内容，再原子移动到最终位置并写 metadata。

## 测试

```bash
uv run pytest
uv run ruff check .
```

测试覆盖 UTF-8、路径与文件名安全、编号不复用、完整生成落盘、失败/不确定状态、重复提交、重新生成、角色归档、Key 不泄露以及 Fish Client 的 HTTP/网络错误映射和不自动 retry。

## 常见错误

- “请先配置 Fish API Key”：在设置页保存 Key。
- 401：Key 无效或已过期。
- 402：余额不足；也可选择 `s2.1-pro-free` 做开发测试。
- 404：`reference_id` 无效或当前 Key 无权访问。
- 429：触发速率/并发限制；程序不会自动重试。
- “结果不确定”：网络在 Fish 可能已收到请求后中断。先检查 Fish 控制台，再决定是否手动重新生成。
- 本地文件缺失：历史仍保留；删除动作可清理数据库记录。

## 已知限制

- V1 是同步单请求 UI；生成期间当前浏览器请求会等待 Fish 返回。
- 角色只在本地校验 reference_id 非空；不会用收费 TTS 验证。连接检查只验证 Key/账户 endpoint。
- 多参考文件会全部用于首次 Clone；角色页只试听第一个本地参考文件，重试 Clone 也只使用该文件。
- 不提供 Fish 远端模型删除；删除/归档本地角色不会删除 Fish 控制台中的私有 voice。
- `pcm` 是裸数据，浏览器原生播放支持因浏览器而异；日常使用优先 `mp3`、`wav` 或 `opus`。
