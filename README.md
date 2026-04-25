<h1 align="center">ChatGPT2API</h1>


<p align="center">ChatGPT2API 主要是对 ChatGPT 官网相关能力进行逆向整理与封装，提供面向 ChatGPT 图片生成、图片编辑、多图组图编辑场景的 OpenAI 兼容图片 API / 代理，并集成在线画图、号池管理、多种账号导入方式与 Docker 自托管部署能力。</p>

> [!WARNING]
> 免责声明：
>
> 本项目涉及对 ChatGPT 官网文本生成、图片生成与图片编辑等相关接口的逆向研究，仅供个人学习、技术研究与非商业性技术交流使用。
>
> - 严禁将本项目用于任何商业用途、盈利性使用、批量操作、自动化滥用或规模化调用。
> - 严禁将本项目用于破坏市场秩序、恶意竞争、套利倒卖、二次售卖相关服务，以及任何违反 OpenAI 服务条款或当地法律法规的行为。
> - 严禁将本项目用于生成、传播或协助生成违法、暴力、色情、未成年人相关内容，或用于诈骗、欺诈、骚扰等非法或不当用途。
> - 使用者应自行承担全部风险，包括但不限于账号被限制、临时封禁或永久封禁以及因违规使用等所导致的法律责任。
> - 使用本项目即视为你已充分理解并同意本免责声明全部内容；如因滥用、违规或违法使用造成任何后果，均由使用者自行承担。

> [!IMPORTANT]
> 本项目基于对 ChatGPT 官网相关能力的逆向研究实现，存在账号受限、临时封禁或永久封禁的风险。请勿使用你自己的重要账号、常用账号或高价值账号进行测试。

## 快速开始

已发布镜像支持 `linux/amd64` 与 `linux/arm64`，在 x86 服务器和 Apple Silicon / ARM Linux 设备上都会自动拉取匹配架构的版本。

```bash
git clone git@github.com:basketikun/chatgpt2api.git
# 按需编辑 config.json 的密钥和 `refresh_account_interval_minute`
# 也可以直接通过环境变量 CHATGPT2API_AUTH_KEY 覆盖 auth-key
docker compose up -d
```

## 功能

### API 兼容能力

- 兼容 `POST /v1/images/generations` 图片生成接口
- 兼容 `POST /v1/images/edits` 图片编辑接口
- 兼容面向图片场景的 `POST /v1/chat/completions`
- 兼容面向图片场景的 `POST /v1/responses`
- `GET /v1/models` 返回 `gpt-image-1` 与 `gpt-image-2`
- 支持通过 `n` 返回多张生成结果

### 在线画图功能

- 内置在线画图工作台，支持生成、图片编辑与多图组图编辑
- 支持 `gpt-image-1` / `gpt-image-2` 模型选择
- 编辑模式支持参考图上传
- 前端支持多图生成交互
- 本地保存图片会话历史，支持回看、删除和清空

### 号池管理功能

- 自动刷新账号邮箱、类型、额度和恢复时间
- 轮询可用账号执行图片生成与图片编辑
- 遇到 Token 失效类错误时自动剔除无效 Token
- 定时检查限流账号并自动刷新
- 支持网页端配置全局 HTTP / HTTPS / SOCKS5 / SOCKS5H 代理
- 支持搜索、筛选、批量刷新、导出、手动编辑和清理账号
- 支持四种导入方式：本地 CPA JSON 文件导入、远程 CPA 服务器导入、`sub2api` 服务器导入、`access_token` 导入
- 支持在设置页配置 `sub2api` 服务器，筛选并批量导入其中的 OpenAI OAuth 账号

### 实验性 / 规划中

- `gpt-image-2` 仍在灰度中，部分能力仍在完善
- 详细状态说明见：[功能清单](./docs/feature-status.en.md)

## Screenshots

文生图界面：

![image](assets/image.png)

编辑图：

![image](assets/image_edit.png)

Cherry Studio 中使用：

![image](assets/chery_studio.png)

号池管理：

![image](assets/account_pool.png)

## API

所有 AI 接口都需要请求头：

```http
Authorization: Bearer <auth-key>
```

<details>
<summary><code>GET /v1/models</code></summary>
<br>

返回当前暴露的图片模型列表。

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer <auth-key>"
```

<details>
<summary>说明</summary>
<br>

| 字段   | 说明                                       |
|:-----|:-----------------------------------------|
| 返回模型 | 当前返回 `gpt-image-1`、`gpt-image-2`         |
| 注意事项 | `gpt-image-2` 当前仍处于灰度 / 实验状态，不保证实际效果完全稳定 |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/generations</code></summary>
<br>

OpenAI 兼容图片生成接口，用于文生图。

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "url",
    "size": "1536x1024",
    "quality": "low",
    "background": "opaque",
    "output_format": "webp",
    "output_compression": 70,
    "moderation": "auto",
    "partial_images": 0
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段                | 说明                                                 |
|:------------------|:---------------------------------------------------|
| `model`           | 图片模型，当前可用值以 `/v1/models` 返回结果为准，推荐使用 `gpt-image-1` |
| `prompt`          | 图片生成提示词                                            |
| `n`               | 生成数量，当前后端限制为 `1-4`                                 |
| `response_format` | 返回格式，支持 `b64_json` 或 `url`，默认 `b64_json`           |
| `size`            | 图片尺寸，支持 `1024x1024`、`1536x1024`、`1024x1536`，也兼容 `1:1`、`16:9`、`9:16` |
| `quality`         | 图片质量，支持 `auto`、`low`、`medium`、`high`               |
| `background`      | 背景模式，支持 `auto`、`transparent`、`opaque`              |
| `output_format`   | 输出格式，支持 `png`、`jpeg`、`webp`                        |
| `output_compression` | 输出压缩率，范围 `0-100`                                |
| `moderation`      | 内容审核等级，支持 `auto`、`low`                            |
| `partial_images`  | 流式中间图数量，范围 `0-3`                                  |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/edits</code></summary>
<br>

OpenAI 兼容图片编辑接口，用于上传图片并生成编辑结果。

```bash
curl http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "size=1024x1536" \
  -F "quality=high" \
  -F "background=opaque" \
  -F "output_format=webp" \
  -F "output_compression=75" \
  -F "input_fidelity=high" \
  -F "image=@./input.png"
```

<details>
<summary>字段说明</summary>
<br>

| 字段       | 说明                                  |
|:---------|:------------------------------------|
| `model`  | 图片模型，推荐使用 `gpt-image-1`             |
| `prompt` | 图片编辑提示词                             |
| `n`      | 生成数量，当前后端限制为 `1-4`                  |
| `image`  | 需要编辑的图片文件，使用 multipart/form-data 上传 |
| `size`   | 图片尺寸，支持 `1024x1024`、`1536x1024`、`1024x1536`，也兼容 `1:1`、`16:9`、`9:16` |
| `quality` | 图片质量，支持 `auto`、`low`、`medium`、`high` |
| `background` | 背景模式，支持 `auto`、`transparent`、`opaque` |
| `output_format` | 输出格式，支持 `png`、`jpeg`、`webp` |
| `output_compression` | 输出压缩率，范围 `0-100` |
| `moderation` | 内容审核等级，支持 `auto`、`low` |
| `partial_images` | 流式中间图数量，范围 `0-3` |
| `input_fidelity` | 参考图保真度，支持 `low`、`high` |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/chat/completions</code></summary>
<br>

面向图片场景的 Chat Completions 兼容接口，不是完整通用聊天代理。

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "messages": [
      {
        "role": "user",
        "content": "生成一张雨夜东京街头的赛博朋克猫"
      }
    ],
    "n": 1,
    "size": "16:9",
    "quality": "high",
    "background": "opaque",
    "output_format": "webp",
    "output_compression": 80
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段         | 说明                   |
|:-----------|:---------------------|
| `model`    | 图片模型，默认按图片生成场景处理     |
| `messages` | 消息数组，需要是图片相关请求内容     |
| `n`        | 生成数量，按当前实现解析为图片数量    |
| `stream`   | 支持流式返回，最终会输出图片内容或 markdown 图片内容 |
| `size` / `quality` / `background` / `output_format` / `output_compression` | 与 `/v1/images/generations` 含义一致 |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/responses</code></summary>
<br>

面向图片生成工具调用的 Responses API 兼容接口，不是完整通用 Responses API 代理。

```bash
curl http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "input": [
      {
        "role": "user",
        "content": [
          { "type": "input_text", "text": "生成一张未来感城市天际线图片" }
        ]
      }
    ],
    "tools": [
      {
        "type": "image_generation",
        "size": "9:16",
        "quality": "medium",
        "background": "opaque",
        "output_format": "png"
      }
    ]
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段       | 说明                            |
|:---------|:------------------------------|
| `model`  | 图片场景推荐使用 `gpt-image-2` 或 `codex-gpt-image-2` |
| `input`  | 输入内容，需要能解析出图片生成提示词            |
| `tools`  | 必须包含 `image_generation` 工具请求，图片参数可放在该工具对象中 |
| `stream` | 支持流式返回 |
| `size` / `quality` / `background` / `output_format` / `output_compression` / `moderation` / `partial_images` | 可放在顶层或 `image_generation` 工具对象中 |
| `input_fidelity` | 编辑场景可用，通常放在 `image_generation` 工具对象中 |

<br>
</details>
</details>

## 社区支持

学 AI , 上 L 站：[LinuxDO](https://linux.do)

## Contributors

感谢所有为本项目做出贡献的开发者：

<a href="https://github.com/basketikun/chatgpt2api/graphs/contributors">
  <img alt="Contributors" src="https://contrib.rocks/image?repo=basketikun/chatgpt2api" />
</a>

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=basketikun/chatgpt2api&type=date&legend=top-left)](https://www.star-history.com/?repos=basketikun%2Fchatgpt2api&type=date&legend=top-left)
