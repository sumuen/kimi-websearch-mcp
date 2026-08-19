# kimi-websearch-mcp

一个基于 MCP Python SDK v2 的 Streamable HTTP 服务。它对外提供供应商无关的
`WebSearch` 和 `WebFetch`，内部使用 Kimi 的搜索与网页提取接口。

## 工具

- `WebSearch(query, count=10)`：返回结构化搜索结果，包括标题、URL、摘要、来源站点和发布日期。
- `WebFetch(url, max_length=20000, start_index=0)`：返回结构化 Markdown 内容；内容较长时，使用返回的
  `next_start_index` 继续读取。

两个工具都是只读、开放世界工具。输入输出由 MCP SDK v2 从 Python 类型生成 JSON Schema。

## 配置 API Key

复制配置模板并填入你的 Kimi API Key：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```dotenv
KIMI_API_KEY=sk-your-real-kimi-api-key
```

`.env` 已被 Git 忽略，不会意外提交真实 Key。

## 启动

```bash
cd kimi-websearch-mcp
uv sync
uv run kimi-websearch-mcp
```

服务仅监听本机，MCP 地址为：

```text
http://127.0.0.1:8000/mcp
```

端口被占用时可改用 `uv run kimi-websearch-mcp --port 8765`；服务仍默认只绑定
`127.0.0.1`。可通过 `--host` 显式修改监听地址。

通用 HTTP MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "kimi-websearch": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

## 验证

```bash
uv run pytest
uv run ruff check .
```
