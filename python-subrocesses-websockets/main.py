import asyncio
import websockets
import docker
from docker import errors as docker_errors
import json
import os
import shutil
from pathlib import Path
import logging
import uuid
import sys

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('server.log')
    ]
)
logger = logging.getLogger(__name__)

class IsolatedSubprocessManager:
    """Gerencia subprocess C em contêiner Docker isolado"""

    def __init__(self, user_id: str, docker_image: str):
        self.user_id = user_id
        self.docker_image = docker_image
        self.container = None
        self.docker_client = None
        self.websocket = None
        self.running = False
        self.user_workspace = None

        # Tentar conectar ao Docker
        try:
            self.docker_client = docker.from_env()
            logger.info("Conectado ao Docker com sucesso")
        except Exception as e:
            logger.error(f"Erro conectando ao Docker: {e}")
            raise

    async def create_user_workspace(self):
        """Cria workspace isolado para o usuário"""
        try:
            self.user_workspace = Path(f"/tmp/user_workspaces/{self.user_id}")
            self.user_workspace.mkdir(parents=True, exist_ok=True)
            logger.info(f"Workspace criado: {self.user_workspace}")

            # Copia arquivos necessários para o workspace do usuário
            source_files = Path("./test")
            if source_files.exists():
                shutil.copytree(source_files, self.user_workspace / "programs", dirs_exist_ok=True)
                logger.info("Arquivos copiados para workspace")
            else:
                logger.warning(f"Diretório ./test não encontrado")
                # Criar um programa de teste simples
                programs_dir = self.user_workspace / "programs"
                programs_dir.mkdir(exist_ok=True)

                # Criar um programa C simples para teste
                test_program = programs_dir / "meu_programa_c"
                with open(test_program, 'w') as f:
                    f.write('#!/bin/bash\necho "Hello from container!"\nread input\necho "You entered: $input"\n')
                os.chmod(test_program, 0o755)
                logger.info("Programa de teste criado")

            # Define permissões restritivas
            os.chmod(self.user_workspace, 0o700)

        except Exception as e:
            logger.error(f"Erro criando workspace: {e}")
            raise

    async def start_container(self, websocket):
        """Inicia contêiner Docker isolado"""
        self.websocket = websocket

        if (self.docker_client == None):
            raise Exception("Error starting docker client")
        try:
            await self.create_user_workspace()

            # Verificar se a imagem existe
            try:
                self.docker_client.images.get(self.docker_image)
                logger.info(f"Imagem Docker encontrada: {self.docker_image}")
            except docker_errors.ImageNotFound:
                logger.warning(f"Imagem {self.docker_image} não encontrada, usando ubuntu:20.04")
                self.docker_image = "ubuntu:20.04"

            # Configurações de segurança do contêiner
            security_opts = [
                "no-new-privileges:true",
            ]

            # Limites de recursos
            mem_limit = "128m"
            cpu_quota = 50000  # 50% de 1 CPU
            cpu_period = 100000

            # Volumes isolados
            volumes = {
                str(self.user_workspace): {
                    'bind': '/workspace',
                    'mode': 'rw'
                }
            }

            # Variáveis de ambiente isoladas
            environment = {
                'USER_ID': self.user_id,
                'HOME': '/workspace',
                'TMPDIR': '/workspace/tmp'
            }

            logger.info(f"Iniciando contêiner para usuário {self.user_id}")


            self.container = self.docker_client.containers.run(
                self.docker_image,
                command=["/bin/bash", "/workspace/programs/meu_programa_c"],
                detach=True,
                stdin_open=True,
                tty=True,
                volumes=volumes,
                environment=environment,
                mem_limit=mem_limit,
                cpu_quota=cpu_quota,
                name=f'executor-{self.user_id}',
                cpu_period=cpu_period,
                security_opt=security_opts,
                network_disabled=True,  # Sem acesso à rede
                read_only=False,  # Permite escrita no workspace
                user="root",  # Mudando para root temporariamente para debug
                cap_drop=["ALL"],  # Remove todas as capabilities
                cap_add=["CHOWN", "SETUID", "SETGID"],  # Adiciona apenas necessárias
                tmpfs={
                    '/tmp': 'size=100m,noexec,nosuid,nodev',
                    '/var/tmp': 'size=100m,noexec,nosuid,nodev'
                },
                remove=True  # Remove automaticamente quando para
            )

            self.running = True
            logger.info(f"Contêiner iniciado com sucesso: {self.container.id} com o nome {self.container.name}")

            # Inicia monitoramento do contêiner
            asyncio.create_task(self._monitor_container())
            asyncio.create_task(self._handle_container_io())

            return True

        except Exception as e:
            logger.error(f"Erro ao iniciar contêiner para {self.user_id}: {e}")
            await self.cleanup()
            return False

    async def _monitor_container(self):
        """Monitora o contêiner para detectar quando para"""
        try:
            logger.debug(f'Container DEBUG - {self.container}')
            while self.running and self.container:
                self.container.reload()
                if self.container.status != 'running':
                    logger.info(f"Contêiner {self.user_id} parou: {self.container.status}")
                    self.running = False
                    break
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Erro monitorando contêiner {self.user_id}: {e}")
            self.running = False

    async def _handle_container_io(self):
        """Lida com I/O do contêiner"""
        try:
            if self.container is None:
                raise Exception("Container não disponível")

            # Método alternativo para I/O
            while self.running and self.container:
                try:
                    # Lê logs do contêiner
                    logs = self.container.logs(stdout=True, stderr=True, stream=False, tail=10)
                    if logs and self.websocket:
                        output = logs.decode('utf-8', errors='ignore')
                        if output.strip():  # Só envia se houver conteúdo
                            await self.websocket.send(json.dumps({
                                "type": "output",
                                "data": output
                            }))
                            logger.info(f"Output enviado: {output[:50]}...")

                except Exception as e:
                    logger.error(f"Erro lendo logs: {e}")

                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Erro no I/O do contêiner {self.user_id}: {e}")

    async def send_input(self, data: str):
        """Envia entrada para o contêiner"""
        if self.container and self.running:
            try:
                logger.info(f"Enviando input: {data}")
                # Método alternativo para enviar input
                result = self.container.exec_run(
                    f'bash -c "echo \'{data}\' > /proc/1/fd/0"',
                    tty=True
                )
                logger.info(f"Input enviado, resultado: {result}")
            except Exception as e:
                logger.error(f"Erro enviando input para {self.container}: {e}")

    async def cleanup(self):
        """Remove contêiner e workspace"""
        logger.info(f"Iniciando cleanup para {self.user_id}")
        self.running = False

        if self.container:
            try:
                logger.info(f'CONTAINER STATUS {self.container.status}')
                if (self.container.status == 'running'):
                    self.container.stop(timeout=5)
                logger.info(f"Contêiner parado: {self.user_id}")
            except Exception as e:
                logger.error(f"Erro parando contêiner {self.user_id}: {e}")

        # Remove workspace do usuário
        if self.user_workspace and self.user_workspace.exists():
            try:
                shutil.rmtree(self.user_workspace)
                logger.info(f"Workspace removido: {self.user_id}")
            except Exception as e:
                logger.error(f"Erro removendo workspace {self.user_id}: {e}")

class SecureWebSocketService:
    """Serviço WebSocket com isolamento completo"""

    def __init__(self, docker_image: str, max_concurrent: int = 20):
        self.docker_image = docker_image
        self.max_concurrent = max_concurrent
        self.active_managers = {}
        self.resource_semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"Serviço inicializado - Imagem: {docker_image}, Max concurrent: {max_concurrent}")

    async def handle_client(self, websocket):
        """Manipula cliente com isolamento completo"""
        client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
        logger.info(f"Nova conexão de {client_ip}")

        async with self.resource_semaphore:
            user_id = str(uuid.uuid4())
            logger.info(f"Gerando sessão {user_id} para cliente {client_ip}")

            try:
                # Cria manager isolado
                manager = IsolatedSubprocessManager(user_id, self.docker_image)
                self.active_managers[user_id] = manager

                if await manager.start_container(websocket):
                    await websocket.send(json.dumps({
                        "type": "connected",
                        "session_id": user_id,
                        "isolation": "docker_container"
                    }))
                    logger.info(f"Cliente {user_id} conectado com sucesso")

                    # Loop de mensagens
                    async for message in websocket:
                        try:
                            logger.info(f"Mensagem recebida de {user_id}: {message[:100]}...")
                            data = json.loads(message)
                            if data.get("type") == "input":
                                await manager.send_input(data.get("data", ""))
                        except json.JSONDecodeError as e:
                            logger.error(f"Erro decodificando JSON: {e}")
                        except Exception as e:
                            logger.error(f"Erro processando mensagem: {e}")
                else:
                    logger.error(f"Falha ao iniciar contêiner para {user_id}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "Falha ao iniciar contêiner"
                    }))

            except websockets.exceptions.ConnectionClosed:
                logger.info(f"Cliente {user_id} desconectou")
            except Exception as e:
                logger.error(f"Erro com cliente isolado {user_id}: {e}")
            finally:
                # Cleanup garantido
                if user_id in self.active_managers:
                    await self.active_managers[user_id].cleanup()
                    del self.active_managers[user_id]
                logger.info(f"Cleanup concluído para {user_id}")


async def main():
    logger.info("=== Iniciando Servidor WebSocket ===")

    # Verificar se Docker está disponível
    try:
        client = docker.from_env()
        client.ping()
        logger.info("Docker está funcionando corretamente")

        # Listar imagens disponíveis
        images = client.images.list()
        logger.info(f"Imagens Docker disponíveis: {[img.tags for img in images if img.tags]}")

    except Exception as e:
        logger.error(f"Erro verificando Docker: {e}")
        return

    service = SecureWebSocketService(
        docker_image="meu-c-program:isolated",  # Vai usar ubuntu:20.04 como fallback
        max_concurrent=20
    )

    try:
        logger.info("Iniciando servidor WebSocket em 0.0.0.0:8765")
        async with websockets.serve(
            service.handle_client,
            "127.0.0.1",
            8765
        ):
            logger.info("Servidor WebSocket iniciado com sucesso!")
            logger.info("Pressione Ctrl+C para parar o servidor")

            # Manter servidor rodando
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Parando servidor...")

    except Exception as e:
        logger.error(f"Erro iniciando servidor: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servidor interrompido pelo usuário")
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
