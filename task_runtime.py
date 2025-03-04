import traceback
from dotenv import load_dotenv
import httpx
import tiktoken
from helper import pad_number, so_far_ms, time_now
from config import (
    MODEL_TYPE_API,
    MODEL_TYPE_AOAI,
    MODEL_TYPE_DS_OLLAMA,
    MODEL_TYPE_DS_FOUNDRY,
    NOT_SUPPORT_STREAM_MODELS,
)
from tables import (
    Tasks,
    create_chunk_table_class,
    create_log_table_class,
    create_request_table_class,
)
from logger import logger
from openai import AzureOpenAI
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential
from ollama import Client
import threading
import uuid
import openai

from task_cache import TaskCache

load_dotenv()


class TaskRuntime:

    def __init__(
        self, task: Tasks, thread_num: int, request_index: int, cache: TaskCache
    ):
        self.task = task
        self.last_token_time = None
        self.thread_num = thread_num
        self.request_index = request_index
        self.cache = cache
        self.stream = False if self.task.model_id in NOT_SUPPORT_STREAM_MODELS else True
        self.Chunks = create_chunk_table_class(task.id)
        self.Logs = create_log_table_class(task.id)

        Requests = create_request_table_class(task.id)
        self.request = Requests(
            id=f"{pad_number(thread_num, task.threads)}{pad_number(request_index, task.request_per_thread)}",
            task_id=self.task.id,
            thread_num=self.thread_num,
            response="",
            chunks_count=0,
            created_at=time_now(),
            output_token_count=0,
            request_index=self.request_index,
            user_id=self.task.user_id,
        )
        self.log("request created")

    def log(self, log_message: str, log_data: dict = None):
        log_item = self.Logs(
            id=f"{uuid.uuid4()}",
            task_id=self.task.id,
            thread_num=self.thread_num,
            request_id=self.request.id,
            log_message=log_message,
            log_data=log_data,
            created_at=time_now(),
        )
        self.cache.log_enqueue(log_item)

    def run_with_timeout(self, method, timeout):
        event = threading.Event()
        error_info = None
        logger.info(f"Starting method {method.__name__} with timeout {timeout} seconds")

        def target():
            nonlocal error_info
            try:
                logger.info(f"Method {method.__name__} started")
                method()
                logger.info(f"Method {method.__name__} completed successfully")
            except Exception as e:
                error_info = traceback.format_exc()
                logger.error(f"Error in Method {method.__name__}: {error_info}")
            finally:
                event.set()
                logger.info(f"Method finished for {method.__name__}")

        thread = threading.Thread(target=target)
        thread.start()
        logger.info(
            f"Waiting for Method {method.__name__} with timeout {timeout} seconds"
        )

        event.wait(timeout)

        if thread.is_alive():
            logger.error(
                f"Timeout occurred while executing Method {method.__name__} after {timeout} seconds"
            )
            raise TimeoutError(
                f"Timeout occurred while executing Method {method.__name__}"
            )
        elif error_info:
            logger.error(f"An error occurred in Method {method.__name__}: {error_info}")
            raise Exception(
                f"An error occurred in Method {method.__name__}:\n{error_info}"
            )

    def num_tokens_from_messages(self):
        tokens_per_message = 3
        num_tokens = 0
        for message in self.task.messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                if value:
                    num_tokens += self.encode(value)
        num_tokens += 3
        return num_tokens

    def encode(self, text):
        if not text:
            return 0

        try:
            encoding = tiktoken.get_encoding("cl100k_base")

            if self.task.model_type == MODEL_TYPE_AOAI:
                encoding = tiktoken.encoding_for_model(self.task.model_id)
            else:
                encoding = tiktoken.encoding_for_model("gpt-4o")

            return len(encoding.encode(text))
        except Exception as e:
            logger.error(f"Error encoding text: {e}")
            return 0

    def latency(self):

        try:
            task_status = self.cache.get_task(self.task.id)
            if not task_status:
                raise Exception("Task not found or was deleted")

            if int(task_status) == 5:
                raise Exception("Task was stopped")

            self.request.input_token_count = self.num_tokens_from_messages()

            self.request.start_req_time = time_now()

            timeout = self.task.timeout / 1000

            if self.task.model_type == MODEL_TYPE_AOAI:
                self.request_aoai()

            elif self.task.model_type == MODEL_TYPE_DS_OLLAMA:
                self.request_ds_ollama()

            elif self.task.model_type == MODEL_TYPE_DS_FOUNDRY:
                self.run_with_timeout(self.request_ds_foundry, timeout)

            elif self.task.model_type == MODEL_TYPE_API:
                self.request_api()

            else:
                raise Exception(f"Model type {self.task.model_type} not supported")

            self.request.end_req_time = time_now()
            self.request.request_latency_ms = (
                self.request.end_req_time - self.request.start_req_time
            )

            if self.request.first_token_latency_ms:
                self.request.last_token_latency_ms = so_far_ms(self.last_token_time)

            self.request.success = 1
        except TimeoutError as e:
            self.request.success = 0
            self.request.response = f"timeout: {self.task.timeout} ms"
            logger.error(f"Timeout Error: {e}", exc_info=True)
        except Exception as e:
            self.request.success = 0
            self.request.response = traceback.format_exc()
            logger.error(f"Error: {e}", exc_info=True)
        finally:
            self.request.completed_at = time_now()
            self.cache.request_enqueue(self.request)

    def request_ds_ollama(self):
        self.log(f"client init start")

        client = Client(
            host=self.task.azure_endpoint,
            headers={"api-key": self.task.api_key if self.task.api_key else ""},
            timeout=httpx.Timeout(self.task.timeout / 1000),
        )

        self.log(f"client request start")
        stream = None

        if self.task.enable_think:
            stream = client.chat(
                model=self.task.model_id,
                messages=self.task.messages_loads,
                stream=True,
                options={"temperature": self.task.temperature},
                max_tokens=self.task.max_tokens,
            )
        else:
            stream = client.chat(
                model=self.task.model_id,
                messages=self.task.messages_loads,
                stream=True,
                format="json",
                options={"temperature": self.task.temperature},
                max_tokens=self.task.max_tokens,
            )

        self.log(f"loop stream start")
        for chunk in stream:
            self.request.chunks_count += 1

            content = chunk["message"]["content"]
            last_token_latency_ms = None

            if not self.request.first_token_latency_ms:
                self.request.first_token_latency_ms = so_far_ms(
                    self.request.start_req_time
                )
                last_token_latency_ms = 0
                self.last_token_time = time_now()
            else:
                last_token_latency_ms = so_far_ms(self.last_token_time)
                self.last_token_time = time_now()

            token_len = 0
            characters_len = 0
            if content:
                logger.info(content)
                self.request.response += content
                token_len = self.encode(content)
                characters_len = len(content)

                self.request.output_token_count += token_len

            chunk_item = self.Chunks(
                id=f"{self.request.id}{pad_number(self.request.chunks_count, 1000000)}",
                chunk_index=self.request.chunks_count,
                thread_num=self.thread_num,
                task_id=self.task.id,
                request_id=self.request.id,
                token_len=token_len,
                characters_len=characters_len,
                created_at=time_now(),
                chunk_content=content,
                request_latency_ms=so_far_ms(self.request.start_req_time),
                last_token_latency_ms=last_token_latency_ms,
            )

            self.cache.chunk_enqueue(chunk_item)

        self.log(f"loop stream end")

    def request_ds_foundry(self):
        self.log(f"client init start")

        client = ChatCompletionsClient(
            endpoint=self.task.azure_endpoint,
            credential=AzureKeyCredential(self.task.api_key),
        )

        self.log(f"client request start")
        response = client.complete(
            stream=True,
            messages=self.task.messages_loads,
            max_tokens=self.task.max_tokens,
            model=self.task.model_id,
            temperature=self.task.temperature,
            timeout=httpx.Timeout(self.task.timeout / 1000),
        )

        self.log(f"loop stream start")
        for update in response:

            if update.choices:
                self.request.chunks_count += 1

                last_token_latency_ms = None
                if not self.request.first_token_latency_ms:
                    self.request.first_token_latency_ms = so_far_ms(
                        self.request.start_req_time
                    )
                    last_token_latency_ms = 0
                    self.last_token_time = time_now()
                else:
                    last_token_latency_ms = so_far_ms(self.last_token_time)
                    self.last_token_time = time_now()

                content = update.choices[0].delta.content

                token_len = 0
                characters_len = 0

                if content:

                    logger.info(content)

                    self.request.response += content
                    token_len = self.encode(content)
                    characters_len = len(content)

                    self.request.output_token_count += token_len

                task_chunk = self.Chunks(
                    id=f"{self.request.id}{pad_number(self.request.chunks_count, 1000000)}",
                    chunk_index=self.request.chunks_count,
                    thread_num=self.thread_num,
                    task_id=self.task.id,
                    request_id=self.request.id,
                    token_len=token_len,
                    characters_len=characters_len,
                    created_at=time_now(),
                    chunk_content=content,
                    request_latency_ms=so_far_ms(self.request.start_req_time),
                    last_token_latency_ms=last_token_latency_ms,
                )

                self.cache.chunk_enqueue(task_chunk)

        self.log(f"loop stream end")
        client.close()

    def request_aoai(self):
        self.log(f"client init start")
        client = AzureOpenAI(
            api_version=self.task.api_version,
            azure_endpoint=self.task.azure_endpoint,
            azure_deployment=self.task.deployment_name,
            api_key=self.task.api_key,
            timeout=httpx.Timeout(self.task.timeout / 1000),
        )

        self.log(f"client request start")
        response = None

        if self.task.model_id in ["o3-mini", "o1-mini", "o1"]:
            response = client.chat.completions.create(
                messages=self.task.messages_loads,
                model=self.task.model_id,
                stream=self.stream,
                max_completion_tokens=self.task.max_tokens,
            )
        else:
            response = client.chat.completions.create(
                messages=self.task.messages_loads,
                model=self.task.model_id,
                stream=self.stream,
                temperature=self.task.temperature,
                max_tokens=self.task.max_tokens,
            )

        self.log(f"loop stream start")
        if not self.stream:
            self.request.response = response.choices[0].message.content

            self.request.first_token_latency_ms = so_far_ms(self.request.start_req_time)

            self.request.request_latency_ms = so_far_ms(self.request.start_req_time)

            self.request.chunks_count = 1

            self.request.output_token_count = self.encode(self.request.response)

        if self.stream:
            for chunk in response:
                if len(chunk.choices) == 0:
                    continue

                self.request.chunks_count += 1
                content = chunk.choices[0].delta.content

                last_token_latency_ms = None
                if not self.request.first_token_latency_ms:
                    self.request.first_token_latency_ms = so_far_ms(
                        self.request.start_req_time
                    )
                    last_token_latency_ms = 0
                    self.last_token_time = time_now()
                else:
                    last_token_latency_ms = so_far_ms(self.last_token_time)
                    self.last_token_time = time_now()

                token_len = 0
                characters_len = 0
                if content:
                    # logger.info(content)

                    self.request.response += content

                    token_len = self.encode(content)
                    characters_len = len(content)

                    self.request.output_token_count += token_len

                task_chunk = self.Chunks(
                    id=f"{self.request.id}{pad_number(self.request.chunks_count, 1000000)}",
                    chunk_index=self.request.chunks_count,
                    thread_num=self.thread_num,
                    task_id=self.task.id,
                    request_id=self.request.id,
                    token_len=token_len,
                    characters_len=characters_len,
                    created_at=time_now(),
                    chunk_content=content,
                    last_token_latency_ms=last_token_latency_ms,
                    request_latency_ms=so_far_ms(self.request.start_req_time),
                )

                self.cache.chunk_enqueue(task_chunk)

        self.log(f"loop stream end")
        client.close()

    def request_api(self):
        self.log(f"client init start")

        client = openai.Client(
            base_url=self.task.azure_endpoint, api_key=self.task.api_key
        )

        self.log(f"client request start")
        response = client.chat.completions.create(
            model=self.task.model_id,
            messages=self.task.messages_loads,
            temperature=self.task.temperature,
            max_tokens=self.task.max_tokens,
            stream=True,
        )

        self.log(f"loop stream start")
        if self.stream:
            for chunk in response:
                if len(chunk.choices) == 0:
                    continue

                self.request.chunks_count += 1
                content = chunk.choices[0].delta.content

                last_token_latency_ms = None
                if not self.request.first_token_latency_ms:
                    self.request.first_token_latency_ms = so_far_ms(
                        self.request.start_req_time
                    )
                    last_token_latency_ms = 0
                    self.last_token_time = time_now()
                else:
                    last_token_latency_ms = so_far_ms(self.last_token_time)
                    self.last_token_time = time_now()

                token_len = 0
                characters_len = 0
                if content:
                    logger.info(content)

                    self.request.response += content

                    token_len = self.encode(content)
                    characters_len = len(content)

                    self.request.output_token_count += token_len

                Chunks = create_chunk_table_class(self.task.id)

                task_chunk = Chunks(
                    id=f"{self.request.id}{pad_number(self.request.chunks_count, 1000000)}",
                    chunk_index=self.request.chunks_count,
                    thread_num=self.thread_num,
                    task_id=self.task.id,
                    request_id=self.request.id,
                    token_len=token_len,
                    characters_len=characters_len,
                    created_at=time_now(),
                    chunk_content=content,
                    last_token_latency_ms=last_token_latency_ms,
                    request_latency_ms=so_far_ms(self.request.start_req_time),
                )

                self.cache.chunk_enqueue(task_chunk)

        self.log(f"loop stream end")
        client.close()
