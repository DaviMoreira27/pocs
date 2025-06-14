import asyncio
import websockets
import json

async def test_client():
    uri = "ws://localhost:8765"

    try:
        async with websockets.connect(uri) as websocket:
            print("Conectado ao servidor!")

            # Aguarda mensagem de conexão
            response = await websocket.recv()
            print(f"Servidor: {response}")

            # Envia input de teste
            await websocket.send(json.dumps({
                "type": "input",
                "data": "Hello from client!"
            }))
            print("Input enviado!")

            # Escuta por mais alguns segundos
            try:
                for i in range(10):
                    response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    print(f"Output: {response}")
            except asyncio.TimeoutError:
                print("Timeout - sem mais mensagens")

    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    asyncio.run(test_client())
