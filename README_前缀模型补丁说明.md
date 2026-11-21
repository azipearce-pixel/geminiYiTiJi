# 模型来源前缀补丁（基于双 Cookie 版本）

本补丁是对已经部署的 `aistudio-api-render` 双 Cookie + 官方 API Key 轮询版本的增量更新。它只需要替换一个文件，不需要重新运行 `apply_patch.py`。

## 前缀规则

- Cookie 账号发现的模型：`pg-<原模型ID>`
- Google AI Studio 官方 API Key 发现的模型：`ais-<原模型ID>`

例如：

- 原 Cookie 模型 `gemini-2.5-pro` 显示为 `pg-gemini-2.5-pro`
- 原官方 Key 模型 `gemini-2.5-flash` 显示为 `ais-gemini-2.5-flash`

`GET /v1/models` 只返回当前已配置渠道实际发现到的模型：只配置 Cookie 时只出现 `pg-`；只配置官方 Key 时只出现 `ais-`；两者都配置时两类都会出现。不会生成空渠道的占位模型。

## 更新步骤

1. 下载并解压本补丁 ZIP。
2. 找到其中的 `src/aistudio_api/api/routes_openai.py`。
3. 在 GitHub 仓库中打开同路径文件：`src/aistudio_api/api/routes_openai.py`，用补丁中的文件完整替换原文件。
4. 提交并推送到原 GitHub 仓库，等待 Render 自动部署。
5. SillyTavern 端点和密码不变，重新刷新模型列表即可看到前缀。

无需改动 Render Environment Variables / Secret Files；无需再次运行 `apply_patch.py`。

## 请求路由

- `pg-...` 只走 Cookie 账号池，不会尝试官方 API Key。
- `ais-...` 只走官方 Google API Key 轮询，不会回退到 Cookie。
- 不带上述前缀的模型 ID 会返回 400，防止误路由。

响应中的 `model` 字段也会保留用户选择的完整前缀模型 ID。
