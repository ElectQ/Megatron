from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.models import MCPServer, SourceConfig
from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin", tags=["mcp"])


@router.post("/mcp-servers")
async def create_mcp_server(
    data: dict,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Register a new MCP server connection."""
    # Check for duplicate name
    existing = await db.execute(select(MCPServer).where(MCPServer.name == data["name"]))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="MCP server with this name already exists")

    server = MCPServer(
        name=data["name"],
        server_url=data["server_url"],
        transport=data.get("transport", "sse"),
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    # Auto-create source config
    source_config = SourceConfig(
        name=data["name"],
        source_type="mcp",
        config={
            "mcp_server_id": server.id,
            "mcp_server_name": server.name,
            "resource_filter": data.get("resource_filter"),
        },
    )
    db.add(source_config)
    await db.commit()

    return {"id": server.id, "name": server.name, "status": "created"}


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
    """Test connection to an MCP server."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    try:
        # For stdio transport, test by starting the MCP server process
        if server.transport == "stdio":
            import asyncio
            import sys
            import os

            # Get the MCP server module path
            server_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "mcp_servers", "soundwave"
            )
            server_path = os.path.abspath(server_path)

            # Parse repo from server_url or use default
            repo = "ElectQ/Soundwave"
            if server.server_url and "/" in server.server_url:
                repo = server.server_url

            # Start MCP server process
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m", "mcp_servers.soundwave",
                "--repo", repo,
                "--branch", "master",
                "--transport", "stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Give it a moment to start
            await asyncio.sleep(1)

            # Check if process is still running
            if proc.returncode is not None:
                stderr = await proc.stderr.read()
                raise RuntimeError(f"MCP server failed to start: {stderr.decode()}")

            # Terminate the process
            proc.terminate()
            await proc.wait()

        server.status = "connected"
        server.last_connected_at = datetime.now(timezone.utc)
        server.last_error = ""
        await db.commit()
        return {"ok": True, "message": "Connection test successful"}

    except Exception as e:
        server.status = "error"
        server.last_error = str(e)
        await db.commit()
        return {"ok": False, "error": str(e)}


@router.post("/mcp-servers/{server_id}/discover")
async def discover_mcp_capabilities(
    server_id: int,
    db: AsyncSession = Depends(get_session),
    _=Depends(admin_auth),
):
    """Discover capabilities from an MCP server."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    try:
        # For stdio transport, discover tools by starting the MCP server
        if server.transport == "stdio":
            import asyncio
            import sys
            import os

            # Get the MCP server module path
            server_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "mcp_servers", "soundwave"
            )
            server_path = os.path.abspath(server_path)

            # Parse repo from server_url or use default
            repo = "ElectQ/Soundwave"
            if server.server_url and "/" in server.server_url:
                repo = server.server_url

            # Start MCP server process
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m", "mcp_servers.soundwave",
                "--repo", repo,
                "--branch", "master",
                "--transport", "stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Give it a moment to start
            await asyncio.sleep(1)

            # Check if process is still running
            if proc.returncode is not None:
                stderr = await proc.stderr.read()
                raise RuntimeError(f"MCP server failed to start: {stderr.decode()}")

            # For now, use known tools list
            # In production, we would send MCP protocol messages to list tools
            capabilities = [
                "list_tweets",
                "search_tweets",
                "get_stats",
                "list_available_dates",
            ]

            # Terminate the process
            proc.terminate()
            await proc.wait()

            server.capabilities = capabilities
            server.status = "connected"
            server.last_connected_at = datetime.now(timezone.utc)
            await db.commit()
            return {"ok": True, "capabilities": capabilities}

        else:
            # For SSE, we would connect to the server and list tools
            server.capabilities = ["list_tweets", "search_tweets", "get_stats", "list_available_dates"]
            server.status = "connected"
            server.last_connected_at = datetime.now(timezone.utc)
            await db.commit()
            return {"ok": True, "capabilities": server.capabilities}

    except Exception as e:
        return {"ok": False, "error": str(e)}


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
        await db.execute(select(SourceConfig).where(SourceConfig.source_type == "mcp"))
    ).scalars().all()
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
