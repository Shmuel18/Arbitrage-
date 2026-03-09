"""
WebSocket Connection Manager
"""

from __future__ import annotations

import logging
from fastapi import WebSocket
from starlette.websockets import WebSocketState
from typing import List

logger = logging.getLogger("trinity.api.ws")


class ConnectionManager:
    """Manages WebSocket connections"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept new WebSocket connection"""
        await websocket.accept()
        self.active_connections.append(websocket)
    
    async def disconnect(self, websocket: WebSocket, close_socket: bool = False) -> None:
        """Remove WebSocket connection"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if close_socket and websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                # Best-effort close; connection is already removed from manager.
                pass
    
    async def broadcast(self, message: str) -> None:
        """Broadcast message to all connected clients"""
        dead: List[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as exc:
                logger.warning("WebSocket send failed; dropping connection: %s", exc)
                dead.append(connection)
        for conn in dead:
            await self.disconnect(conn, close_socket=True)
