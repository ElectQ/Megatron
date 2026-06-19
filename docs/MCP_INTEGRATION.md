# MCP Integration Architecture

## Overview

Megatron 的所有数据来源统一通过 **MCP (Model Context Protocol)** 协议接入。

MCP 是 Anthropic 推出的开放协议，用于标准化 LLM 应用与外部数据源、工具的交互方式。通过 MCP，Megatron 可以以统一的方式接入任何数据源，无需为每个来源编写特定的集成代码。

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Megatron Core                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────┐             │
│  │  Analysis   │  │   Storage   │  │  Tasks  │             │
│  │   Engine    │  │   (Items)   │  │Scheduler│             │
│  └──────┬──────┘  └─────────────┘  └─────────┘             │
│         │                                                    │
│  ┌──────┴──────────────────────────────────┐              │
│  │         Unified Ingest Interface          │              │
│  │  (MCP Client / MCPSource Adapter)          │              │
│  └──────┬──────────────────────────────────┘              │
└─────────┼────────────────────────────────────────────────────┘
          │
┌─────────┴────────────────────────────────────────────────────┐
│              MCP Server Ecosystem                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │Soundwave │ │ Telegram │ │  RSS     │ │  Custom  │  ...    │
│  │  MCP     │ │  MCP     │ │  MCP     │ │  MCP     │         │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. MCPSource Adapter

位于 `src/megatron/plugins/sources/base.py`，是 Megatron 与 MCP Server 交互的适配器：

- 封装 MCP 客户端连接细节
- 将 MCP resources 转换为标准化的 `Item` 数据模型
- 支持 SSE 和 stdio 两种传输模式
- 自动发现 MCP Server 的 capabilities

### 2. MCP Server Registry

数据库表 `mcp_servers` 存储所有连接的 MCP Server：

| 字段 | 说明 |
|------|------|
| `name` | 服务器名称（唯一） |
| `server_url` | MCP Server URL 或命令 |
| `transport` | 传输协议：`sse` 或 `stdio` |
| `capabilities` | 支持的 resources/tools 列表 |
| `status` | 连接状态：`connected` / `disconnected` / `error` |

### 3. Source Configuration

数据库表 `source_configs` 存储数据源配置：

| 字段 | 说明 |
|------|------|
| `name` | 配置名称 |
| `source_type` | 固定为 `mcp` |
| `config` | JSON 配置：关联的 MCP Server、资源过滤器等 |
| `enabled` | 是否启用 |

## Data Flow

1. **注册 MCP Server**：通过 UI 或 API 添加 MCP Server 连接信息
2. **发现能力**：Megatron 连接 MCP Server，获取支持的 resources
3. **创建 Source Config**：基于 MCP Server 创建数据源配置，指定资源过滤器
4. **数据同步**：Scheduler 或手动触发，通过 MCP 协议拉取数据
5. **数据标准化**：MCPSource 将原始数据转换为 `Item` 模型，存入数据库

## Migration Path

### 现有 Soundwave 集成

Soundwave 需要封装为 MCP Server：

```python
# Soundwave MCP Server (未来实现)
from mcp.server import Server
from mcp.types import Resource

app = Server("soundwave")

@app.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(uri="tweets://daily", name="Daily Tweets"),
        Resource(uri="tweets://list/{list_id}", name="Twitter List"),
    ]

@app.read_resource()
async def read_resource(uri: str) -> str:
    # 返回 Soundwave 抓取的数据
    pass
```

### 部署模式

Soundwave MCP Server 可以部署在：

- **GitHub Actions**：定时触发，执行完即停，通过 SSE endpoint 暴露数据
- **VPS/云服务器**：常驻服务，持续提供数据
- **本地开发**：stdio 模式，本地调试

Megatron 作为 MCP Client，不关心 Soundwave 跑在哪里，只通过 MCP 协议消费数据。

## API Endpoints

### MCP Server Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/mcp-servers` | 注册 MCP Server |
| GET | `/api/admin/mcp-servers` | 列出所有 MCP Server |
| POST | `/api/admin/mcp-servers/{id}/test` | 测试连接 |
| POST | `/api/admin/mcp-servers/{id}/discover` | 发现 capabilities |
| DELETE | `/api/admin/mcp-servers/{id}` | 删除 MCP Server |

### Source Config Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/source-configs` | 列出所有 source configs |

## Future Data Sources

任何新数据源只需：

1. 实现 MCP Server（暴露 resources）
2. 在 Megatron UI 中添加 MCP Server 连接
3. 配置资源过滤器
4. 开始同步数据

不需要修改 Megatron 核心代码。

## UI Changes

- **导航**："Data Sources" 改为 "Integrations"
- **Sources 页面**：改为 MCP Server 管理界面
- **添加流程**：支持输入 MCP Server URL、选择传输协议、测试连接、发现能力

## Dependencies

```toml
[project]
dependencies = [
    # ... existing dependencies
    "mcp>=1.0.0",
]
```

## Next Steps

1. [ ] 安装 `mcp` Python 包
2. [ ] 实现 MCP 客户端连接逻辑（`MCPSource._get_client`）
3. [ ] 实现 capability 发现（`discover_capabilities`）
4. [ ] 实现数据拉取（`fetch`）
5. [ ] 将 Soundwave 封装为 MCP Server
6. [ ] 测试端到端数据流
