"""
server.py

FastAPI application that serves the xterm.js frontend and bridges
browser WebSocket connections to a PTY running your game.

Each browser connection spawns its own independent game process,
so players have fully isolated game state.
"""

import asyncio
import os
import ptyprocess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

# Serve static files (index.html, etc.) from the ./static directory.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """Serve the xterm.js terminal page."""
    return FileResponse("static/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle one browser connection.

    Spawns a new game process in a PTY, then relays data in both
    directions until the browser disconnects or the game exits.
    """
    await websocket.accept()

    # Spawn your game in a PTY. The PTY makes the process believe
    # it's running in a real terminal, so readline, ANSI codes, and
    # isatty() checks all behave correctly.
    proc = ptyprocess.PtyProcessUnicode.spawn(
        ["python", "main.py"],
        env={**os.environ, "TERM": "xterm-256color"}
    )

    async def pty_to_browser():
        """Read game output and forward it to the browser."""
        loop = asyncio.get_event_loop()
        while proc.isalive():
            try:
                # Read from PTY in a thread so we don't block the event loop.
                data = await loop.run_in_executor(None, proc.read, 1024)
                await websocket.send_text(data)
            except EOFError:
                break  # Game exited cleanly

    async def browser_to_pty():
        """Read browser keystrokes and forward them to the game."""
        while proc.isalive():
            try:
                data = await websocket.receive_text()
                proc.write(data)
            except WebSocketDisconnect:
                break  # Browser disconnected

    # Run both directions concurrently.
    await asyncio.gather(pty_to_browser(), browser_to_pty())

    # Clean up the game process when the connection ends.
    if proc.isalive():
        proc.terminate()