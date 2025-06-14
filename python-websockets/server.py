import asyncio
from websockets import serve


async def echo(websocket):
    async for message in websocket:
        await websocket.send("Teste message")


async def main():
    async with serve(echo, "localhost", 8765) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
