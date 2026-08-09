"""
WEBSOCKET — Lane holati o'zgarganda frontendga DARHOL xabar beradi.

Frontend (PremiumPage) shu manzilga ulanadi:
  wss://server/ws/orders/{orderId}?token=<initData>

Har safar lane_worker.py da _set_status chaqirilganda, shu yerdagi
broadcast() funksiyasi orqali barcha ulangan mijozlarga yuboriladi —
frontend "Lane"larni polling qilmasdan, real vaqtda yangilaydi.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        # order_id -> ulangan WebSocket'lar ro'yxati
        self._connections: dict[int, list[WebSocket]] = {}

    async def connect(self, order_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(order_id, []).append(ws)

    def disconnect(self, order_id: int, ws: WebSocket) -> None:
        conns = self._connections.get(order_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns and order_id in self._connections:
            del self._connections[order_id]

    async def broadcast(self, order_id: int, payload: dict) -> None:
        """lane_worker.py dan chaqiriladi — Lane holati o'zgarganda."""
        conns = self._connections.get(order_id, [])
        dead = []
        for ws in conns:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(order_id, ws)


manager = ConnectionManager()


@router.websocket("/ws/orders/{order_id}")
async def order_ws(websocket: WebSocket, order_id: int):
    await manager.connect(order_id, websocket)
    try:
        while True:
            # frontend hech narsa yubormaydi, faqat tinglaydi —
            # ulanishni ochiq ushlab turish uchun kutamiz
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(order_id, websocket)
