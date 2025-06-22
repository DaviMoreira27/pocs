import asyncio
from docker.models.containers import ContainerCollection
from typing import List, Optional, Callable
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
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('server.log')
    ]
)
logger = logging.getLogger(__name__)

class InteractiveProcess:
    """Gerencia um processo interativo com stdin/stdout em tempo real"""

    def __init__(self, container, docker_client, command, workdir="/workspace"):
        self.container = container
        self.docker_client = docker_client
        self.command = command
        self.workdir = workdir
        self.exec_id = None
        self.socket = None
        self.running = False
        self.stdin_queue = asyncio.Queue()
        self.output_callbacks = []
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def start(self):
        """Inicia o processo interativo"""
        try:
            # Criar exec com stdin habilitado
            self.exec_id = self.docker_client.api.exec_create(
                self.container.id,
                self.command,
                stdout=True,
                stderr=True,
                stdin=True,
                tty=False,
                workdir=self.workdir
            )

            # Iniciar exec e obter socket
            self.socket = self.docker_client.api.exec_start(
                self.exec_id['Id'],
                detach=False,
                tty=False,
                stream=True,
                socket=True
            )

            self.running = True

            # Iniciar tasks para gerenciar stdin e stdout
            asyncio.create_task(self._handle_stdin())
            asyncio.create_task(self._handle_stdout())

            logger.info(f"Processo interativo iniciado: {self.command}")
            return True

        except Exception as e:
            logger.error(f"Erro iniciando processo interativo: {e}")
            return False

    async def _handle_stdin(self):
        """Gerencia envio de dados para stdin do processo"""
        try:
            while self.running and self.socket:
                try:
                    # Aguardar dados na queue com timeout
                    data = await asyncio.wait_for(self.stdin_queue.get(), timeout=1.0)
                    if data is None:  # Sinal para parar
                        break

                    # Enviar dados para stdin em thread separada
                    await asyncio.get_event_loop().run_in_executor(
                        self._executor,
                        self._send_to_stdin,
                        data
                    )

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Erro enviando stdin: {e}")
                    break

        except Exception as e:
            logger.error(f"Erro no handler de stdin: {e}")

    def _send_to_stdin(self, data):
        """Envia dados para stdin (executa em thread separada)"""
        try:
            if self.socket and self.socket._sock:
                self.socket._sock.send(data.encode('utf-8'))
        except Exception as e:
            logger.error(f"Erro enviando dados para stdin: {e}")

    async def _handle_stdout(self):
        """Gerencia leitura de stdout/stderr do processo"""
        try:
            def read_output():
                """Lê output em thread separada"""

                output_chunks = []
                if (not self.socket):
                    raise Exception("Socket not defined")

                try:
                    for chunk in self.socket:
                        if chunk:
                            output_chunks.append(chunk)
                        if not self.running:
                            break
                except Exception as e:
                    logger.error(f"Erro lendo output: {e}")
                return output_chunks

            # Executar leitura em thread separada
            chunks = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                read_output
            )

            # Processar chunks recebidos
            with open('output.log', 'a', encoding='utf-8') as log_file:
                for chunk in chunks:
                    if chunk:
                        decoded_chunk = chunk.decode('utf-8', errors='ignore')

                        # Exibir em tempo real
                        print(decoded_chunk, end='', flush=True)

                        # Salvar no arquivo
                        log_file.write(decoded_chunk)
                        log_file.flush()

                        # Chamar callbacks registrados
                        for callback in self.output_callbacks:
                            try:
                                await callback(decoded_chunk)
                            except Exception as e:
                                logger.error(f"Erro no callback de output: {e}")

        except Exception as e:
            logger.error(f"Erro no handler de stdout: {e}")
        finally:
            self.running = False

    async def send_input(self, data: str):
        """Envia dados para stdin do processo"""
        if self.running:
            await self.stdin_queue.put(data)
        else:
            logger.warning("Tentativa de enviar input para processo não ativo")

    def add_output_callback(self, callback: Callable):
        """Adiciona callback para ser chamado quando houver output"""
        self.output_callbacks.append(callback)

    async def stop(self):
        """Para o processo"""
        self.running = False
        await self.stdin_queue.put(None)  # Sinal para parar stdin handler

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        # Aguardar threads terminarem
        self._executor.shutdown(wait=True)

    def get_exit_code(self):
        """Obtém o código de saída do processo"""
        if self.exec_id:
            try:
                exec_info = self.docker_client.api.exec_inspect(self.exec_id['Id'])
                return exec_info.get('ExitCode')
            except:
                return None
        return None

class IsolatedSubprocessManager:

    def __init__(self, user_id: str, docker_image: str):
        self.user_id = user_id
        self.docker_image = docker_image
        self.container = None
        self.docker_client = None
        self.running = False
        self.user_workspace = None
        self.active_processes = {}  # Dicionário para processos ativos

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
                (self.user_workspace / "programs").mkdir(exist_ok=True)
                test_file = self.user_workspace / "programs" / "test.c"
                test_file.write_text('#include <stdio.h>\nint main() {\n    printf("Hello World!\\n");\n    return 0;\n}')
                logger.info("Arquivo test.c criado automaticamente")

            os.chmod(self.user_workspace, 0o755)

        except Exception as e:
            logger.error(f"Erro criando workspace: {e}")
            raise

    async def start_container(self):
        if (self.docker_client == None):
            self.docker_client = docker.from_env()

        try:
            await self.create_user_workspace()

            try:
                self.docker_client.images.get(self.docker_image)
                logger.info(f"Imagem Docker encontrada: {self.docker_image}")
            except docker_errors.ImageNotFound:
                logger.warning(f"Imagem {self.docker_image} não encontrada, usando ubuntu:20.04")
                self.docker_image = "ubuntu:20.04"

            security_opts = ["no-new-privileges:true"]
            mem_limit = "1024m"
            cpu_quota = 50000
            cpu_period = 100000

            volumes = {
                str(self.user_workspace): {
                    'bind': '/workspace',
                    'mode': 'rw'
                }
            }

            environment = {
                'USER_ID': self.user_id,
                'HOME': '/workspace',
                'TMPDIR': '/workspace/tmp'
            }

            logger.info(f"Iniciando contêiner para usuário {self.user_id}")

            self.container = self.docker_client.containers.run(
                self.docker_image,
                command=["/bin/bash", "-c", "while true; do sleep 1; done"],
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
                read_only=False,
                user="root",
                cap_drop=["ALL"],
                cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
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

    async def start_interactive_process(self, process_id: str, command: str, output_callback=None):
        """Inicia um processo interativo"""
        if not self.container or not self.running:
            logger.error("Container não está rodando")
            return False

        if process_id in self.active_processes:
            logger.warning(f"Processo {process_id} já está ativo")
            return False

        try:
            process = InteractiveProcess(
                self.container,
                self.docker_client,
                command
            )

            if output_callback:
                process.add_output_callback(output_callback)

            success = await process.start()
            if success:
                self.active_processes[process_id] = process
                logger.info(f"Processo interativo {process_id} iniciado: {command}")
                return True
            else:
                logger.error(f"Falha ao iniciar processo {process_id}")
                return False

        except Exception as e:
            logger.error(f"Erro iniciando processo interativo {process_id}: {e}")
            return False

    async def send_input_to_process(self, process_id: str, data: str):
        """Envia dados para stdin de um processo específico"""
        if process_id not in self.active_processes:
            logger.warning(f"Processo {process_id} não encontrado")
            return False

        try:
            await self.active_processes[process_id].send_input(data)
            return True
        except Exception as e:
            logger.error(f"Erro enviando input para processo {process_id}: {e}")
            return False

    async def stop_process(self, process_id: str):
        """Para um processo específico"""
        if process_id not in self.active_processes:
            logger.warning(f"Processo {process_id} não encontrado")
            return False

        try:
            process = self.active_processes[process_id]
            await process.stop()
            del self.active_processes[process_id]
            logger.info(f"Processo {process_id} parado")
            return True
        except Exception as e:
            logger.error(f"Erro parando processo {process_id}: {e}")
            return False

    async def execute_command(self, command: str):
        """Executa comando simples (não interativo)"""
        if not self.container or not self.running:
            logger.error("Container não está rodando")
            return None

        try:
            logger.info(f"Executando comando: {command}")

            exec_result = self.container.exec_run(
                command,
                stdout=True,
                stderr=True,
                stream=True,
                tty=False,
                workdir="/workspace"
            )

            full_output = []
            with open('output.log', 'a', encoding='utf-8') as log_file:
                for chunk in exec_result.output:
                    if chunk:
                        decoded_chunk = chunk.decode('utf-8', errors='ignore')
                        print(decoded_chunk, end='', flush=True)
                        log_file.write(decoded_chunk)
                        log_file.flush()
                        full_output.append(decoded_chunk)

            exit_code = exec_result.exit_code
            complete_output = ''.join(full_output)

            logger.info(f"Comando executado. Exit code: {exit_code}")

            return {
                'exit_code': exit_code,
                'output': complete_output,
                'command': command
            }

        except Exception as e:
            logger.error(f"Erro executando comando '{command}': {e}")
            return None

    async def cleanup(self):
        """Remove contêiner e workspace"""
        logger.info(f"Iniciando cleanup para {self.user_id}")
        self.running = False

        # Parar todos os processos ativos
        for process_id in list(self.active_processes.keys()):
            await self.stop_process(process_id)

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

# Exemplo de uso com WebSocket
async def websocket_handler(websocket, path):
    """Handler para WebSocket que gerencia processos interativos"""
    sp = IsolatedSubprocessManager("websocket_user", "gcc:latest")

    async def output_callback(data):
        """Callback para enviar output via WebSocket"""
        try:
            await websocket.send(json.dumps({
                'type': 'output',
                'data': data
            }))
        except Exception as e:
            logger.error(f"Erro enviando output via WebSocket: {e}")

    try:
        # Iniciar container
        success = await sp.start_container()
        if not success:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': 'Falha ao iniciar container'
            }))
            return

        await websocket.send(json.dumps({
            'type': 'ready',
            'message': 'Container pronto'
        }))

        # Loop principal do WebSocket
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')

                if msg_type == 'start_process':
                    process_id = data.get('process_id')
                    command = data.get('command')
                    success = await sp.start_interactive_process(process_id, command, output_callback)

                    await websocket.send(json.dumps({
                        'type': 'process_started' if success else 'error',
                        'process_id': process_id,
                        'success': success
                    }))

                elif msg_type == 'send_input':
                    process_id = data.get('process_id')
                    input_data = data.get('data')
                    success = await sp.send_input_to_process(process_id, input_data)

                    if not success:
                        await websocket.send(json.dumps({
                            'type': 'error',
                            'message': f'Falha ao enviar input para processo {process_id}'
                        }))

                elif msg_type == 'stop_process':
                    process_id = data.get('process_id')
                    success = await sp.stop_process(process_id)

                    await websocket.send(json.dumps({
                        'type': 'process_stopped',
                        'process_id': process_id,
                        'success': success
                    }))

                elif msg_type == 'execute_command':
                    command = data.get('command')
                    result = await sp.execute_command(command)

                    await websocket.send(json.dumps({
                        'type': 'command_result',
                        'result': result
                    }))

            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': 'Formato JSON inválido'
                }))
            except Exception as e:
                logger.error(f"Erro processando mensagem WebSocket: {e}")
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': str(e)
                }))

    except websockets.exceptions.ConnectionClosed:
        logger.info("Conexão WebSocket fechada")
    except Exception as e:
        logger.error(f"Erro no handler WebSocket: {e}")
    finally:
        await sp.cleanup()

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
    """Exemplo de uso local para testes"""
    sp = IsolatedSubprocessManager("user_03", "gcc:latest")

    try:
        # Iniciar container
        success = await sp.start_container()
        if not success:
            logger.error("Falha ao iniciar container")
            return

        await asyncio.sleep(2)

        # Callback para capturar output
        async def print_output(data):
            print(f"[OUTPUT]: {data}", end='')

        # Iniciar processo interativo
        success = await sp.start_interactive_process("test_proc", "python3 -u -c 'import sys; print(\"Digite algo:\"); linha = input(); print(f\"Você digitou: {linha}\")'", output_callback=print_output)

        if success:
            # Simular entrada do usuário após 3 segundos
            await asyncio.sleep(30)
            await sp.send_input_to_process("test_proc", "Hello World!\n")

            # Aguardar mais um pouco
            await asyncio.sleep(5)

            # Parar processo
            await sp.stop_process("test_proc")

    except Exception as e:
        logger.error(f"Erro durante execução: {e}")
    finally:
        await sp.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        general_cleanup()


###
#
#  type: tipo do comando a ser enviado - 'start_process' | 'stop_process' | 'send_input' | 'cleanup'
#
#   process_id: o script que recebera o comando
#   command: o comando em si
#
#
#
