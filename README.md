# AI Studio Render：OpenAI 兼容接口 + Render Secret 配置补丁

本补丁是**增量覆盖包**，不是完整的 aistudio-api 源码仓库。它基于 `azipearce-pixel/aistudio-api-render` 当前 `master` 结构，新增 OpenAI 兼容接口，并通过 `apply_patch.py` 对原项目 `src/aistudio_api/api/app.py` 做少量自动接线。原 Gemini `/v1beta` 路由、Web 管理台、Cookie 账号调度及 WAA signer 不需要重写。

## 1. 下载并解压原仓库

1. 打开原仓库：<https://github.com/azipearce-pixel/aistudio-api-render>
2. 点击 **Code → Download ZIP**，解压到本地。
3. 确认解压后的项目根目录中能看到 `main.py`、`Dockerfile`、`render.yaml` 和 `src/`。

## 2. 应用本补丁

1. 将本补丁 ZIP 解压到上述项目根目录，保留目录结构；出现同名文件时允许覆盖。
2. 在项目根目录打开终端（Windows 可在文件夹地址栏输入 `cmd` 后回车）。
3. 执行：

```bash
python apply_patch.py
```

看到以下提示即表示接线完成：

```text
[OK] OpenAI-compatible routes mounted at /v1
[OK] Render Cookie Secret File import wired into startup
```

补丁新增/覆盖的文件：

- `src/aistudio_api/api/routes_openai.py`：`/v1/models`、`/v1/chat/completions`。
- `src/aistudio_api/api/render_secrets.py`：从 Render 环境变量/Secret File 导入 Cookie。
- `src/aistudio_api/api/app.py`：由补丁脚本自动增加路由挂载和 Cookie 导入钩子。

## 3. 提交回你自己的 GitHub 仓库

1. 将修改后的项目推送到你用于 Render 部署的 GitHub 仓库。
2. 确认提交包含 `apply_patch.py`、`README_部署说明.md`、两个新增 Python 文件，以及已修改的 `src/aistudio_api/api/app.py`。
3. Render 如果已经连接这个仓库并开启 Auto-Deploy，会自动开始部署；否则在 Render 服务页面手动点 **Manual Deploy → Deploy latest commit**。

> 不要把 Cookie、Google API Key、`.env` 或包含凭据的 Secret File 上传到 GitHub。补丁包本身不含任何密钥。

## 4. 在 Render 配置 Cookie

进入 **Render Dashboard → 你的 Web Service → Environment**。

推荐使用 **Secret Files**，可以配置多枚 Cookie，每枚 Cookie 会作为独立账号导入原有账号池：

1. 分别登录两个 Google AI Studio 账号，用 Cookie-Editor 各自导出完整 JSON。
2. Render 的 **Secret Files → Add Secret File** 添加两份文件：
   - Filename：`cookie1.json`，Contents：第一个账号导出的完整 JSON。
   - Filename：`cookie2.json`，Contents：第二个账号导出的完整 JSON。
3. 在 **Environment Variables → Add Environment Variable** 添加：

| Key | Value |
|---|---|
| `AISTUDIO_COOKIE1_FILE` | `/etc/secrets/cookie1.json` |
| `AISTUDIO_COOKIE2_FILE` | `/etc/secrets/cookie2.json` |

完成后保存并重新部署即可。后台账号池中会分别显示 `Render Cookie 1` 和 `Render Cookie 2`，由原项目的账号调度机制管理。最多支持编号 1–10，例如 `AISTUDIO_COOKIE3_FILE` 对应 `/etc/secrets/cookie3.json`。

旧配置仍然兼容：`AISTUDIO_COOKIE_FILE` + `/etc/secrets/aistudio_cookie.json` 仍可作为额外的单个 Cookie 导入；也支持 `AISTUDIO_COOKIE` 直接放 Cookie 内容。多 Cookie 推荐使用上面的编号方式，不需要把 Cookie 粘贴到环境变量值中。

可选项：

| Key | 作用 | 默认值 |
|---|---|---|
| `AISTUDIO_COOKIE_TIER` | Cookie 账号初始订阅等级，后续原账号服务仍会尝试刷新识别 | `Free` |
| `AISTUDIO_COOKIE_AUTH_USER` | AI Studio 多登录身份的用户序号 | `0` |
| `AISTUDIO_COOKIE_NAME` | 管理后台账号列表中显示的名称 | `Render Secret Cookie` |

每枚 Cookie 会在每次服务启动时按固定账号 ID 导入已有账号库，不会每次启动都创建新账号。原项目已有的 Cookie 轮换任务仍负责运行时更新会话 Cookie。

## 5. 在 Render 配置 Google AI Studio 官方 API Key

同样推荐使用 **Secret Files**：

1. Render → **Environment → Secret Files → Add Secret File**。
2. Filename：`google_api_keys.txt`。
3. Contents：每行一枚 Google AI Studio 官方 API Key，例如：

```text
AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
AIzaSyYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
AIzaSyZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ
```

4. 在 **Environment Variables** 添加：

| Key | Value |
|---|---|
| `GOOGLE_API_KEYS_FILE` | `/etc/secrets/google_api_keys.txt` |

也可以不用 Secret File，直接设置 `GOOGLE_API_KEYS` 环境变量，多枚 Key 用英文逗号、分号或换行分隔。也兼容 `GOOGLE_AI_STUDIO_API_KEYS`、`GEMINI_API_KEYS`，以及单枚 Key 的 `GOOGLE_API_KEY` / `GEMINI_API_KEY`。

## 6. 保留网页密码作为 SillyTavern 的 API Key

原项目的 `AISTUDIO_WEB_PASSWORD` 同时被原 API 鉴权逻辑接受为 API Key。因此 SillyTavern 中直接填写你已经设置的 **AISTUDIO_WEB_PASSWORD** 即可，不必把 Google 官方 API Key 放进 SillyTavern。

注意：

- `AISTUDIO_WEB_PASSWORD` 是你自己服务的下游访问密码。
- `GOOGLE_API_KEYS` 是服务端访问 Google 官方 Gemini API 的上游密钥。
- 两者用途不同，不要把 Google 官方 Key 填进客户端。

## 7. 部署后检查

部署日志中不应出现 Cookie 或 API Key 原文。先检查：

```text
https://你的服务名.onrender.com/health
```

应返回原项目的健康检查响应。

然后在本地 PowerShell / 终端测试模型列表（把占位密码替换为你的 `AISTUDIO_WEB_PASSWORD`）：

```bash
curl -H "Authorization: Bearer YOUR_AISTUDIO_WEB_PASSWORD" \
  https://你的服务名.onrender.com/v1/models
```

应返回 OpenAI 格式的模型列表：`{"object":"list","data":[...]}`。列表来自两部分：

- Cookie 账号通过原 AI Studio 模型发现机制识别到的模型。
- 每一枚官方 API Key 分别调用 Google `ListModels` 后汇总的模型。

若多个来源都有同名模型，只显示一条；调用时优先尝试官方 API Key，所有 Key 都失败后再回退 Cookie。

## 8. SillyTavern 设置

进入 SillyTavern 的 **API Connections**：

| 项目 | 填写内容 |
|---|---|
| API | `Chat Completion` / OpenAI-compatible |
| Source / Provider | OpenAI（或兼容 OpenAI 的自定义服务） |
| Custom Endpoint / Base URL | `https://你的服务名.onrender.com/v1` |
| API Key | 你的 `AISTUDIO_WEB_PASSWORD` |
| Model | 点击刷新模型列表，然后选择模型 |

你的 Render 服务如果名称为 `aistudio-api-illj`，Base URL 就是：

```text
https://aistudio-api-illj.onrender.com/v1
```

不要再选 Google Gemini 原生连接类型；这次新增的是 OpenAI Chat Completions 兼容接口。

## 9. Key 轮询规则

- 每一次真正发往 Google 的上游尝试都会消耗一个轮询位置；成功、HTTP 报错、连接异常都一样。
- 当前 Key 失败会立即取下一枚 Key 尝试；同一条请求最多尝试当前配置的每枚 Key 一次，避免无限重试。
- 下一条请求会从上一条请求最后一次实际尝试的 Key 后面继续，不会因前一条请求发生故障而反复卡在同一枚 Key。
- `/v1/models` 会分别读取所有 Key 的模型权限并合并结果；模型发现过程本身不消耗聊天请求的轮询位置。
- 流式请求在收到 Google 的成功响应头之前可以自动切换 Key；如果流已经开始输出文本后连接中断，无法安全地从另一枚 Key 重放而不造成重复内容，因此会向客户端报告流中断，不会从头重试。

## 10. 支持范围与注意事项

此补丁主要面向 SillyTavern 的 OpenAI Chat Completion 文本对话：支持 `/v1/models`、`/v1/chat/completions`、system/developer/user/assistant/tool 消息、常用采样参数、JSON 输出模式、基本函数声明，以及 Base64 Data URL 输入图片。它**不是完整的 OpenAI API 重实现**，不包含 `/v1/responses`、Embeddings、Audio API、图像生成 API 等独立端点；远程 HTTP 图片 URL 需要客户端改为 Base64 Data URL。函数调用声明和非流式函数调用结果有基础转换支持，流式函数调用增量目前不支持。

官方 Key 模型清单按 Key 实际返回的 `ListModels` 结果过滤 `generateContent` 能力；Cookie 清单来自当前已导入账号的 AI Studio 模型发现结果。Google 可能因项目状态、地区、配额、API 版本或账号权限拒绝某个模型，模型出现在列表中不代表该次调用一定有配额。

### Render 免费实例与 Cookie 持久性

Render Secret Files 会在运行时挂载到 `/etc/secrets/<filename>`，服务每次启动会重新从 Secret File 导入 Cookie，因此 Web 管理后台里临时导入的 Cookie 不再是唯一来源。

但原项目运行时轮换后的 Cookie 写入 `/app/data/accounts`。Render 免费实例没有持久磁盘，服务休眠、重建或重启后这部分运行时更新可能丢失，启动时只能重新导入 Secret File 中保存的初始 Cookie。Secret File 是只读配置来源，应用不会把轮换后的 Cookie 自动写回 Render 控制台；若初始 Cookie 已经失效，需重新导出一份有效 Cookie 并更新 Secret File。若要长期保存自动轮换后的 Cookie 和账号状态，应使用支持 Persistent Disk 的 Render 服务，并把磁盘挂载到 `/app/data`。Secret File 本身可留在 Render，不要把 Cookie 提交到 Git。现有 `/health` 路由是 Render 服务健康检查接口；它本身不是进程内保活任务。Render 官方说明免费 Web Service 在 15 分钟没有入站流量后会休眠，唤醒通常约需一分钟；若要避免休眠，需要外部定时访问服务（例如监控服务每 10–14 分钟请求一次 `/health`），或改用付费实例。Render 也可能在任何时候重启免费实例。详见 [Render Free 服务说明](https://render.com/docs/free)。

## 11. 常见问题

**`/v1/models` 返回 401**：检查 SillyTavern / curl 是否使用 `Authorization: Bearer <AISTUDIO_WEB_PASSWORD>`，并确认 Render 上已保存并部署该环境变量。

**模型列表只有一部分**：确认 `GOOGLE_API_KEYS_FILE` 指向 `/etc/secrets/google_api_keys.txt`，Secret File 文件名大小写完全一致，并查看 Render 日志中是否有某枚 Key 的 ListModels 读取失败提示（日志不会打印 Key 本身）。

**Cookie 模型列表为空**：确认 `AISTUDIO_COOKIE_FILE` 路径正确，Secret File JSON 是 Cookie-Editor 导出的 JSON，而不是浏览器导出的 HAR 文件；并确认 Cookie 对应的 AI Studio 会话仍有效。

**能列出模型但生成报错**：这通常是上游权限、配额、模型当前状态或请求参数造成的。官方 Key 会按轮询顺序自动尝试下一枚；若同一模型也存在 Cookie 来源，所有 Key 失败后会回退 Cookie。

**SillyTavern 仍然只显示直连**：确认 API 类型选择的是 OpenAI-compatible / Chat Completion，而不是 Google Gemini 原生协议；Base URL 必须带 `/v1`。
