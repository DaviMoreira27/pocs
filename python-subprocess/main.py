import asyncio
from docker.models.containers import ContainerCollection
from typing import List
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

    def __init__(self, user_id: str, docker_image: str):
        self.user_id = user_id
        self.docker_image = docker_image
        self.container = None
        self.docker_client = None
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

            source_files = Path("./test")
            if source_files.exists():
                shutil.copytree(source_files, self.user_workspace / "programs", dirs_exist_ok=True)
                logger.info("Arquivos copiados para workspace")
            else:
                logger.warning(f"Diretório ./test não encontrado")
                # Criar um arquivo de teste se não existir
                (self.user_workspace / "programs").mkdir(exist_ok=True)
                test_file = self.user_workspace / "programs" / "test.c"
                test_file.write_text('#include <stdio.h>\nint main() {\n    printf("Hello World!\\n");\n    return 0;\n}')
                logger.info("Arquivo test.c criado automaticamente")

            os.chmod(self.user_workspace, 0o755)  # Mudança: permissões mais permissivas

        except Exception as e:
            logger.error(f"Erro criando workspace: {e}")
            raise

    async def start_container(self):
        if (self.docker_client == None):
            raise Exception("Error starting docker client")
        try:
            await self.create_user_workspace()

            try:
                self.docker_client.images.get(self.docker_image)
                logger.info(f"Imagem Docker encontrada: {self.docker_image}")
            except docker_errors.ImageNotFound:
                logger.warning(f"Imagem {self.docker_image} não encontrada, usando ubuntu:20.04")
                self.docker_image = "ubuntu:20.04"

            # Configurações de segurança mais permissivas para testes
            security_opts = [
                "no-new-privileges:true",
            ]

            mem_limit = "1024m"
            cpu_quota = 50000
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
                command=["/bin/bash", "-c", "while true; do sleep 1; done"],  # Comando para manter container vivo
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
                network_disabled=True,
                read_only=False,  # Mudança: permitir escrita
                user="root",
                cap_drop=["ALL"],
                cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],  # Capabilities mínimas necessárias
                tmpfs={
                    '/tmp': 'size=100m',
                    '/var/tmp': 'size=100m'
                },
                remove=True
            )

            self.running = True
            logger.info(f"Contêiner iniciado com sucesso: {self.container.id}")

            return True

        except Exception as e:
            logger.error(f"Erro ao iniciar contêiner para {self.user_id}: {e}")
            await self.cleanup()
            return False

    async def execute_command(self, command: str):
        """Executa um comando no container e retorna a saída"""
        if not self.container or not self.running:
            logger.error("Container não está rodando")
            return None

        try:
            logger.info(f"Executando comando: {command}")

            # Usar exec_run para executar comando e capturar saída
            result = self.container.exec_run(
                command,
                stdout=True,
                stderr=True,
                tty=False,  # Importante: TTY=False para capturar saída corretamente
                workdir="/workspace"
            )

            # Decodificar saída
            output = result.output.decode('utf-8', errors='ignore')
            exit_code = result.exit_code

            logger.info(f"Comando executado. Exit code: {exit_code}")
            logger.info(f"Saída: {output}")

            return {
                'exit_code': exit_code,
                'output': output,
                'command': command
            }

        except Exception as e:
            logger.error(f"Erro executando comando '{command}': {e}")
            return None

    async def execute_interactive_session(self, commands: List[str]):
        """Executa uma sessão interativa com múltiplos comandos"""
        results = []

        for cmd in commands:
            result = await self.execute_command(cmd)
            if result:
                results.append(result)
            await asyncio.sleep(0.1)  # Pequena pausa entre comandos

        return results

    async def _monitor_container(self):
        """Monitora o status do container"""
        try:
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

    async def cleanup(self):
        """Remove contêiner e workspace"""
        logger.info(f"Iniciando cleanup para {self.user_id}")
        self.running = False

        if self.container:
            try:
                self.container.reload()
                if self.container.status == 'running':
                    self.container.stop(timeout=5)
                logger.info(f"Contêiner parado: {self.user_id}")
            except Exception as e:
                logger.error(f"Erro parando contêiner {self.user_id}: {e}")

        if self.user_workspace and self.user_workspace.exists():
            try:
                shutil.rmtree(self.user_workspace)
                logger.info(f"Workspace removido: {self.user_id}")
            except Exception as e:
                logger.error(f"Erro removendo workspace {self.user_id}: {e}")

def general_cleanup():
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True, filters={'name': 'executor-'})

        for container in containers:
            try:
                container.remove(force=True)
                logger.info(f"Container removido: {container.name}")
            except Exception as e:
                logger.error(f"Erro removendo container {container.name}: {e}")

        logger.info("Cleanup de containers concluído")
    except Exception as e:
        logger.error(f"Erro no cleanup geral: {e}")

async def main():
    try:
        client = docker.from_env()
        client.ping()

        images = client.images.list()
        logger.info(f"Imagens Docker disponíveis: {[img.tags for img in images if img.tags]}")

        client.containers.prune()
        logger.info("Docker está funcionando corretamente")
    except Exception as e:
        logger.error(f"Erro verificando Docker: {e}")
        return

    sp = IsolatedSubprocessManager("user_03", "gcc:latest")
    try:
        # Iniciar container
        success = await sp.start_container()
        if not success:
            logger.error("Falha ao iniciar container")
            return

        # Aguardar um pouco para o container estabilizar
        await asyncio.sleep(2)

        # Lista de comandos para testar
        test_commands = [
            # "pwd",
            # "ls -la /workspace",
            "ls /workspace/programs",
            # "cat /workspace/programs/test.c",
            # "gcc --version",
            # "gcc /workspace/programs/test.c -o /workspace/programs/test",
            # "./workspace/programs/test"
            "/workspace/programs/script"
        ]

        # Executar comandos
        logger.info("=== Iniciando execução de comandos ===")
        for cmd in test_commands:
            result = await sp.execute_command(cmd)
            if result:
                print(f"\n>>> {cmd}")
                print(f"Exit Code: {result['exit_code']}")
                print(f"Output:\n{result['output']}")
                print("-" * 50)
            else:
                print(f"Falha ao executar: {cmd}")

        # Manter container rodando por um tempo para monitoramento
        logger.info("Monitorando container por 10 segundos...")
        await asyncio.sleep(10)

    except Exception as e:
        logger.error(f"Erro durante execução: {e}")
    finally:
        await sp.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        general_cleanup()
