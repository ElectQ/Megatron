from __future__ import annotations

import shlex
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.models import MCPServer, SourceConfig
from ..core.security import admin_auth, encrypt_config

router = APIRouter(prefix="/api/admin", tags=["mcp"])

# Source names that collide with built-in plugin kinds; disallowed for configs
# so a SourceConfig never shadows a registry kind, and short enough to fit the
# analysis_modules.source column (String(32)).
RESERVED_SOURCE_NAMES = {"twitter", "soundwave", "mcp"}


class MCPServerIn(BaseModel):
    name: str
    server_url: str = ""
    transport: str = "sse"
    resource_filter: str | None = None


class MCPTestIn(BaseModel):
    server_url: str = ""
    transport: str = "sse"


def _mcp_source_from_config(transport: str, server_url: str, source_label: str = "test"):
    """Build an MCPSource from stored config, mirroring the runner's routing.

    stdio: a command line ("cmd arg arg") is split into command/args; an
    "owner/repo" string routes to the bundled soundwave server; empty falls
    back to the bundled default. sse: the URL is the endpoint.
    """
    from ..plugins.sources.base import MCPSource

    url = server_url or ""
    if transport == "sse":
        return MCPSource(transport="sse", server_url=url, source_label=source_label)
    if " " in url:
        parts = shlex.split(url)
        return MCPSource(
            transport="stdio", command=parts[0], args=parts[1:], source_label=source_label
        )
    if "/" in url:
        return MCPSource(transport="stdio", repo=url, source_label=source_label)
    return MCPSource(transport="stdio", source_label=source_label)


def _validate_source_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(name) > 32:
        raise HTTPException(status_code=400, detail="name must be at most 32 characters")
    if name in RESERVED_SOURCE_NAMES:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved source name")
    return name


@router.post("/mcp-servers")
async def create_mcp_server(
    body: MCPServerIn,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Register a new MCP server connection (+ its auto source config)."""
    name = _validate_source_name(body.name)
    existing = await db.execute(select(MCPServer).where(MCPServer.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="MCP server with this name already exists")

    # Single transaction: flush to obtain server.id, then commit both rows
    # together so a failure can't leave an orphaned MCPServer.
    server = MCPServer(name=name, server_url=body.server_url, transport=body.transport)
    db.add(server)
    await db.flush()

    source_config = SourceConfig(
        name=name,
        source_type="mcp",
        config=encrypt_config(
            {
                "mcp_server_id": server.id,
                "mcp_server_name": server.name,
                "resource_filter": body.resource_filter,
            }
        ),
    )
    db.add(source_config)
    await db.commit()
    await db.refresh(server)

    return {"id": server.id, "name": server.name, "status": "created"}


@router.post("/mcp-servers/test")
async def test_mcp_config(body: MCPTestIn, _=Depends(admin_auth)):
    """Stateless 'test before save': connect with unsaved config, list tools."""
    source = _mcp_source_from_config(body.transport, body.server_url)
    try:
        caps = await source.discover_capabilities()
        return {"ok": True, "capabilities": [t["name"] for t in caps.get("tools", [])]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        await source.close()


@router.get("/mcp-servers")
async def list_mcp_servers(
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """List all registered MCP servers."""
    result = await db.execute(select(MCPServer).order_by(MCPServer.created_at.desc()))
    servers = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "server_url": s.server_url,
            "transport": s.transport,
            "capabilities": s.capabilities,
            "status": s.status,
            "last_connected_at": s.last_connected_at.isoformat() if s.last_connected_at else None,
            "created_at": s.created_at.isoformat(),
        }
        for s in servers
    ]


@router.post("/mcp-servers/{server_id}/test")
async def test_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Test connection to a saved MCP server via the real MCP protocol."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    source = _mcp_source_from_config(server.transport, server.server_url)
    try:
        caps = await source.discover_capabilities()
        tools = [t["name"] for t in caps.get("tools", [])]
        server.status = "connected"
        server.last_connected_at = datetime.now(timezone.utc)
        server.last_error = ""
        await db.commit()
        return {"ok": True, "message": "Connection successful", "capabilities": tools}
    except Exception as e:
        server.status = "error"
        server.last_error = str(e)[:500]
        await db.commit()
        return {"ok": False, "error": str(e)[:300]}
    finally:
        await source.close()


@router.post("/mcp-servers/{server_id}/discover")
async def discover_mcp_capabilities(
    server_id: int,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Enumerate a saved MCP server's tools via the real MCP protocol."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    source = _mcp_source_from_config(server.transport, server.server_url)
    try:
        caps = await source.discover_capabilities()
        tools = [t["name"] for t in caps.get("tools", [])]
        server.capabilities = tools
        server.status = "connected"
        server.last_connected_at = datetime.now(timezone.utc)
        server.last_error = ""
        await db.commit()
        return {"ok": True, "capabilities": tools}
    except Exception as e:
        server.status = "error"
        server.last_error = str(e)[:500]
        await db.commit()
        return {"ok": False, "error": str(e)[:300]}
    finally:
        await source.close()


@router.delete("/mcp-servers/{server_id}")
async def delete_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Delete an MCP server and its associated source configs."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    # Delete associated source configs (dialect-neutral: filter the small MCP set
    # in Python rather than relying on DB-specific JSON operators).
    mcp_configs = (
        (await db.execute(select(SourceConfig).where(SourceConfig.source_type == "mcp")))
        .scalars()
        .all()
    )
    for sc in mcp_configs:
        if str((sc.config or {}).get("mcp_server_id")) == str(server_id):
            await db.delete(sc)

    await db.delete(server)
    await db.commit()
    return {"ok": True}


@router.get("/source-configs")
async def list_source_configs(
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """List all source configurations."""
    result = await db.execute(select(SourceConfig).order_by(SourceConfig.created_at.desc()))
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "source_type": c.source_type,
            "config": c.config,
            "enabled": c.enabled,
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in configs
    ]
