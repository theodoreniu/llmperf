from dotenv import load_dotenv
import tiktoken
from helper import data_id, redis_client, so_far_ms, time_now
from serialize import chunk_enqueue, request_enqueue
from config import aoai
from ollama import Client

from sqlalchemy.orm.session import Session

from tables import Chunks
from tables import Requests
from tables import Tasks


from logger import logger

load_dotenv()


class TaskRuntime:

    def __init__(
        self,
        task: Tasks,
        thread_num: int,
        encoding: tiktoken.Encoding,
        client,
        request_index: int
    ):
        self.task = task
        self.last_token_time = None
        self.thread_num = thread_num
        self.encoding = encoding
        self.client = client
        self.request_index = request_index
        self.redis = redis_client()

    def num_tokens_from_messages(self, task: Tasks):
        messages = task.query
        tokens_per_message = 3
        num_tokens = 0
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(self.encoding.encode(value))
        num_tokens += 3
        return num_tokens

    def deal_aoai(self, task_request: Chunks) -> Chunks:
        response = self.client.chat.completions.create(
            messages=self.task.query,
            model=self.task.model_id,
            stream=True,
            temperature=self.task.temperature,
            max_tokens=self.task.content_length
        )

        for chunk in response:
            if len(chunk.choices) == 0:
                continue

            task_chunk = Chunks(
                id=data_id(),
                task_id=self.task.id,
                thread_num=self.thread_num,
                request_id=task_request.id,
                token_len=0,
                characters_len=0,
                created_at=time_now(),
            )

            if not task_request.first_token_latency_ms:
                task_request.first_token_latency_ms = so_far_ms(
                    task_request.start_req_time)
                task_chunk.last_token_latency_ms = 0
                self.last_token_time = time_now()
            else:
                task_chunk.last_token_latency_ms = so_far_ms(
                    self.last_token_time
                )
                self.last_token_time = time_now()

            task_request.chunks_count += 1

            delta = chunk.choices[0].delta
            task_chunk.chunk_content = delta.content

            if task_chunk.chunk_content:
                print(task_chunk.chunk_content, end="", flush=True)
                task_request.response += task_chunk.chunk_content
                task_chunk.token_len += len(
                    self.encoding.encode(task_chunk.chunk_content))
                task_chunk.characters_len += len(task_chunk.chunk_content)

                task_request.output_token_count += len(
                    self.encoding.encode(task_chunk.chunk_content))

            task_chunk.request_latency_ms = so_far_ms(
                task_request.start_req_time
            )

            task_chunk.chunk_index = task_request.chunks_count

            chunk_enqueue(self.redis, task_chunk)

        return task_request

    def deal_ds(self, task_request: Chunks) -> Chunks:

        client = Client(
            host=self.task.azure_endpoint,
            headers={
                'api-key': self.task.api_key
            },
        )

        stream = client.chat(
            model=self.task.model_id,
            messages=self.task.query,
            stream=True,
            options={
                "temperature": self.task.temperature
            },
        )

        for chunk in stream:
            task_chunk = Chunks(
                id=data_id(),
                task_id=self.task.id,
                thread_num=self.thread_num,
                request_id=task_request.id,
                token_len=0,
                characters_len=0,
                created_at=time_now(),
            )

            if not task_request.first_token_latency_ms:
                task_request.first_token_latency_ms = so_far_ms(
                    task_request.start_req_time)
                task_chunk.last_token_latency_ms = 0
                self.last_token_time = time_now()
            else:
                task_chunk.last_token_latency_ms = so_far_ms(
                    self.last_token_time
                )
                self.last_token_time = time_now()

            task_chunk.chunk_content = chunk['message']['content']

            if task_chunk.chunk_content:
                print(task_chunk.chunk_content, end="", flush=True)
                task_request.response += task_chunk.chunk_content
                task_chunk.token_len += len(
                    self.encoding.encode(task_chunk.chunk_content))
                task_chunk.characters_len += len(task_chunk.chunk_content)

                task_request.output_token_count += len(
                    self.encoding.encode(task_chunk.chunk_content))

            task_request.chunks_count += 1

            task_chunk.request_latency_ms = so_far_ms(
                task_request.start_req_time
            )

            task_chunk.chunk_index = task_request.chunks_count

            chunk_enqueue(self.redis, task_chunk)

        return task_request

    def latency(self):

        task_request = Requests(
            id=data_id(),
            task_id=self.task.id,
            thread_num=self.thread_num,
            response="",
            chunks_count=0,
            created_at=time_now(),
            output_token_count=0,
            request_index=self.request_index
        )

        try:
            task_request.input_token_count = self.num_tokens_from_messages(
                self.task)

            task_request.start_req_time = time_now()

            if self.task.model_type == aoai:
                task_request = self.deal_aoai(task_request)
            else:
                task_request = self.deal_ds(task_request)

            task_request.end_req_time = time_now()
            task_request.request_latency_ms = (
                task_request.end_req_time - task_request.start_req_time)

            if task_request.first_token_latency_ms:
                task_request.last_token_latency_ms = so_far_ms(
                    self.last_token_time
                )

            task_request.success = 1

        except Exception as e:
            task_request.success = 0
            task_request.response = f"{e}"
            logger.error(f'Error: {e}', exc_info=True)

        task_request.completed_at = time_now()

        request_enqueue(self.redis, task_request)
