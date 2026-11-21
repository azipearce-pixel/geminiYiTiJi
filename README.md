# Google AI Studio → SillyTavern 云反代

把 Google AI Studio 的 Gemini 模型接入 SillyTavern。你只需要 **Fork 本仓库 → 在 Render 部署 → 添加自己的 Cookie 或 Google API Key → 在 SillyTavern 填写接口地址**，不需要自己修改代码或购买服务器。

> 本项目部署的是你自己的独立服务。Cookie、Google API Key 和 SillyTavern 访问密码都由你自己配置，请勿把这些凭据提交到 GitHub。

## 功能

- **SillyTavern 兼容**：提供 OpenAI Chat Completions 接口，可在酒馆中使用 `Chat Completion` 连接。
- **两种上游凭据**：支持 Google AI Studio 网页 Cookie，以及 Google AI Studio 官方 Gemini API Key。
- **多凭据轮询**：支持配置多枚 Cookie 和多枚官方 API Key；官方 Key 请求失败时会尝试下一枚。
- **按实际权限列出模型**：模型列表分别从已配置的 Cookie 账号和 API Key 查询，不会把预设模型冒充成可用模型。
- **精简模型列表**：只展示 Gemini 3.1–4.0 范围内、符合聊天生成条件的 Gemini 模型；不展示 Gemini 3.0、2.x、Gemma 或图片、语音、视频等专用模型。
- **区分凭据渠道**：`pg-` 模型只使用 Cookie，`ais-` 模型只使用官方 API Key，不会跨渠道回退。
- **Render 健康检查**：部署配置已设置 `/health`。

## 一、准备工作

开始前，你需要：

1. 一个 GitHub 账号。
2. 一个 Render 账号：<https://render.com/>。
3. 至少一种可用的上游凭据：
   - Google AI Studio 网页登录 Cookie；或
   - Google AI Studio 官方 Gemini API Key。

Cookie 和 API Key 可以同时配置，也可以只配置其中一种。两种方式的模型会在 SillyTavern 中分别显示。

## 二、Fork 本仓库

1. 登录 GitHub，打开本项目仓库：<https://github.com/azipearce-pixel/geminiYiTiJi>。
2. 点击页面右上角的 **Fork**。
3. 在 Fork 页面选择你自己的 GitHub 账号，创建仓库。
4. Fork 完成后，你会得到一个属于自己的仓库，例如：`https://github.com/你的用户名/gemini`。

后续部署请使用你自己 Fork 出来的仓库，而不是直接部署原仓库。

## 三、在 Render 部署

本仓库已经包含 `render.yaml`，Render 会自动读取部署配置。**请使用 Blueprint 部署，不要手动新建普通 Web Service。**

1. 登录 <https://dashboard.render.com/>。
2. 点击左上角 **New +**，选择 **Blueprint**。
3. 连接 GitHub，并授权 Render 访问你刚才 Fork 的仓库。
4. 选择自己的 `aistudio-api-render` 仓库和 `master` 分支。
5. Render 会自动读取 `render.yaml`。确认服务配置后继续。
6. 当 Render 要求填写 `AISTUDIO_WEB_PASSWORD` 时，设置一段你自己保存的强密码，然后创建并部署。
   - 如果创建时没有出现输入框，部署完成后进入服务的 **Environment** 页面，手动添加同名变量和值，再保存部署。
7. 等待首次构建和部署完成。可在 Render 服务的 **Events** 或 **Logs** 页面查看状态。

仓库中的部署配置已经设置好 Docker 构建方式、启动命令、`10000` 端口和 `/health` 健康检查；通常不需要自行填写 Build Command、Start Command 或端口。

部署成功后，Render 会分配一个服务地址，格式类似：

```text
https://你的服务名称.onrender.com
```

请记下这个地址，后面配置 SillyTavern 时会用到。

## 四、配置上游凭据

部署完成后，打开 Render Dashboard → 你的 Web Service → **Environment**。推荐通过 **Secret Files** 保存凭据，避免把密钥直接写进 GitHub 仓库。

### 方式 A：使用 Google AI Studio Cookie

适合希望通过 Google AI Studio 网页账号使用模型的用户。

#### 1. 导出 Cookie

1. 在浏览器中登录 <https://aistudio.google.com/>，确认可以正常打开 AI Studio。
2. 在该浏览器中使用 Cookie-Editor 等 Cookie 导出扩展。
3. 在 `aistudio.google.com` 页面导出 Cookie，格式选择 **JSON**。
4. 复制或保存完整导出内容。不要把 Cookie 发给他人。

#### 2. 上传到 Render Secret Files

1. 在 Render 服务的 **Environment** 页面找到 **Secret Files**。
2. 点击 **Add File**。
3. `Filename` 填写 `aistudio_cookie1.json`。
4. 将刚才导出的完整 JSON 内容粘贴到 `Contents`。
5. 保存。

#### 3. 添加对应环境变量

在同一个 **Environment** 页面找到 **Environment Variables**，添加：

| Key | Value |
|---|---|
| `AISTUDIO_COOKIE1_FILE` | `/etc/secrets/aistudio_cookie1.json` |

完成后保存并等待 Render 重新部署。服务启动时会自动导入这枚 Cookie。

#### 4. 添加更多 Cookie（可选）

最多支持编号 1–10。每枚 Cookie 使用一个独立的 Secret File 和环境变量，例如：

| Secret File 文件名 | Environment Variable | Value |
|---|---|---|
| `aistudio_cookie1.json` | `AISTUDIO_COOKIE1_FILE` | `/etc/secrets/aistudio_cookie1.json` |
| `aistudio_cookie2.json` | `AISTUDIO_COOKIE2_FILE` | `/etc/secrets/aistudio_cookie2.json` |
| `aistudio_cookie3.json` | `AISTUDIO_COOKIE3_FILE` | `/etc/secrets/aistudio_cookie3.json` |

后续依此类推。每个 Cookie 会作为独立账号加入账号池。

**订阅等级（可选）**：如果 Cookie 对应的 AI Studio 账号是 Pro 或 Ultra，可在 Environment Variables 中添加 `AISTUDIO_COOKIE_TIER`，Value 填写 `Pro` 或 `Ultra`。不设置时默认为 `Free`。请按 Cookie 对应账号的实际订阅等级填写。

### 方式 B：使用 Google AI Studio 官方 API Key

适合希望直接通过 Google Gemini API 使用模型的用户。

1. 打开 Google AI Studio 的 API Key 管理页面：<https://aistudio.google.com/apikey>。
2. 创建或复制你自己的 Gemini API Key。
3. 回到 Render 服务的 **Environment → Secret Files**。
4. 点击 **Add File**。
5. `Filename` 填写 `google_api_keys.txt`。
6. 在 `Contents` 中粘贴 API Key。多枚 Key 请每行放一枚，例如：

```text
你的第一枚Google_API_Key
你的第二枚Google_API_Key
你的第三枚Google_API_Key
```

7. 保存后，在 **Environment Variables** 中添加：

| Key | Value |
|---|---|
| `GOOGLE_API_KEYS_FILE` | `/etc/secrets/google_api_keys.txt` |

8. 保存并等待 Render 重新部署。

服务启动后会读取文件中的所有 Key，并在模型查询和请求时使用。多枚 Key 会轮询；某枚 Key 请求失败时，会继续尝试其他已配置的 Key。

> **安全提醒：** Google API Key 是上游访问凭据，可能关联你的配额或计费项目。请妥善保管，并在 Google AI Studio 中留意自己的使用量和计费设置。不要把 Key 填入 SillyTavern 的 API Key 栏。

### 两种方式都配置

Cookie 和官方 API Key 可以同时使用。只需分别完成方式 A 和方式 B。服务会把两种来源的模型分别列出，不会混在一起或互相代替。

## 五、在 SillyTavern 中连接

等 Render 部署完成、并且至少配置好一种上游凭据后，打开 SillyTavern：

1. 进入 **API Connections（API 连接）**。
2. API 类型选择 **Chat Completion**。
3. 服务提供方选择 **OpenAI** 或 OpenAI 兼容接口。
4. 在自定义端点 / Base URL 中填写：

   ```text
   https://你的服务名称.onrender.com/v1
   ```

5. API Key 留空，因此**千万不要随意把服务器网址交给他人！**
6. 点击刷新模型列表，选择需要使用的模型，然后保存连接。

**注意：** 这里使用的是 OpenAI 兼容的 Chat Completion 接口，不要选择 Google Gemini 原生 API 连接类型。

## 六、模型前缀说明

模型名称前缀代表它所使用的凭据来源：

| 模型前缀 | 凭据来源 | 实际请求渠道 |
|---|---|---|
| `pg-` | Google AI Studio 网页 Cookie | 只使用 Cookie 账号 |
| `ais-` | Google AI Studio 官方 API Key | 只使用官方 API Key |

例如，同一个 Gemini 模型如果两个渠道都能提供，可能会分别出现：

```text
pg-gemini-3.8-flash
ais-gemini-3.8-flash
```

它们是两个独立选项。选择 `pg-` 不会自动改用官方 API Key；选择 `ais-` 也不会改用 Cookie。

模型列表会根据配置的 Cookie 账号和 API Key 实际查询结果生成，并进行 Gemini 3.1–4.0 聊天模型筛选。模型目录约缓存 60 秒；刚添加凭据或修改权限后，可稍等片刻再刷新模型列表。

> 模型出现在列表中，表示对应凭据的模型目录返回了该模型并通过筛选；这不代表每次调用都一定成功。Google 仍可能因账号权限、项目状态、地区、配额或计费限制拒绝具体请求。

## 七、检查服务是否正常

在浏览器中打开：

```text
https://你的服务名称.onrender.com/health
```

如果服务正常运行，会返回健康检查响应。Render 的 Blueprint 已将 `/health` 配置为健康检查路径。

如果 SillyTavern 无法连接，请依次检查：

- Render 的首次部署是否已经成功，服务状态是否为 Live。
- Base URL 是否以 `/v1` 结尾，且没有多余空格或重复斜杠。
- 是否至少配置了一种有效的 Cookie 或官方 API Key。
- Secret File 文件名、路径和环境变量是否逐字对应。
- Render 保存配置后是否完成了新一轮部署。

## 八、常见问题

### 1. SillyTavern 刷新模型列表后没有任何模型

请检查是否已经添加 Cookie 或官方 API Key，并确认对应 Secret File 的环境变量路径正确。Cookie 必须来自仍然有效的 AI Studio 登录会话；官方 API Key 必须能够访问 Gemini API。修改凭据后等待部署完成，再刷新模型列表。

### 2. 模型列表只有 `pg-`，没有 `ais-`

说明当前 Cookie 渠道发现了符合条件的模型，但官方 API Key 渠道没有查询到符合筛选条件的模型。检查 `google_api_keys.txt` 内容和 `GOOGLE_API_KEYS_FILE` 路径，并确认 Key 本身有效。

### 3. 模型列表只有 `ais-`，没有 `pg-`

说明官方 API Key 渠道发现了模型，但 Cookie 渠道没有查询到符合条件的模型。检查 Cookie 是否完整、是否仍有效，以及 `AISTUDIO_COOKIE1_FILE` 是否指向正确的文件。

### 4. 模型能显示，但调用时返回 403、404 或配额错误

模型是否可见与本次请求是否一定能成功并不是一回事。请检查对应凭据的模型权限、Google 项目状态、订阅等级、地区限制和剩余配额。`pg-` 与 `ais-` 使用不同凭据渠道，请根据模型前缀检查对应账号或 API Key。

### 5. 免费 Render 服务第一次打开很慢，或一段时间不用后要等待

Render Free Web Service 在连续 15 分钟没有收到入站流量后会自动休眠；下一次请求会唤醒服务，通常需要约一分钟。免费实例的本地文件系统也是临时的，服务休眠、重启或重新部署时，本地运行数据可能丢失。Render 中配置的 Secret Files 不会因为应用本地文件丢失而变成 GitHub 文件；服务再次启动时会重新读取这些 Secret Files。若需要持久保存运行时账号状态和统计数据，需要使用支持 Persistent Disk 的付费实例，并按需配置磁盘。

## 九、安全与使用须知

- **不要将 Cookie、API Key、`AISTUDIO_WEB_PASSWORD` 或其他私密凭据提交到 GitHub。** 只在 Render 的 Environment / Secret Files 中填写。
- 不要将自己的 Render 服务地址和访问密码公开给不可信任的人；任何获得访问密码的人都可能使用你配置的上游额度。
- Google 上游服务的可用性、模型访问权限、速率限制和计费规则由 Google 决定；请自行遵守 Google AI Studio / Gemini API 的相关条款。
- Render Free 有休眠、临时文件系统和月度使用量限制，适合个人测试和轻量使用，不保证持续在线。

## 相关链接

- [本项目 GitHub 仓库](https://github.com/azipearce-pixel/geminiYiTiJi)
- [Render Blueprint 部署文档](https://render.com/docs/blueprint-spec)
- [Render 环境变量与 Secret Files 文档](https://render.com/docs/configure-environment-variables)
- [Render 免费服务限制](https://render.com/docs/free)
- [Google AI Studio](https://aistudio.google.com/)
- [Google Gemini API Key 管理](https://aistudio.google.com/apikey)
