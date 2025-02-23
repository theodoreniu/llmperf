
from tables import Tasks
from task_runtime import TaskRuntime
from theodoretools.bot import feishu_text
from concurrent.futures import ThreadPoolExecutor
from config import aoai
import tiktoken
from openai import AzureOpenAI, OpenAI
from logger import logger


def safe_create_and_run_task(task: Tasks, thread_num: int,  encoding: tiktoken.Encoding, client, request_index: int):
    task_runtime = TaskRuntime(
        task=task,
        thread_num=thread_num,
        encoding=encoding,
        client=client,
        request_index=request_index
    )
    task_runtime.latency()


def task_executor(task: Tasks):

    if task.feishu_token:
        feishu_text(
            f"start to run {task.source_location} {task.target_location} {task.request_per_thread} {task.threads} {task.model_id}",
            task.feishu_token
        )

    encoding = tiktoken.get_encoding("cl100k_base")
    client = None

    if task.model_type == aoai:
        encoding = tiktoken.encoding_for_model(task.model_id)
        client = AzureOpenAI(
            api_version=task.api_version,
            azure_endpoint=task.azure_endpoint,
            azure_deployment=task.deployment_name,
            api_key=task.api_key,
            timeout=task.timeout,
        )
    else:
        # todo: change to ds
        encoding = tiktoken.encoding_for_model('gpt-4o')
        client = OpenAI(
            base_url=task.azure_endpoint,
            api_key=task.api_key or "",
            timeout=task.timeout,
        )

    with ThreadPoolExecutor(max_workers=task.threads) as executor:
        futures = [
            executor.submit(safe_create_and_run_task, task,
                            thread_index + 1, encoding, client, request_index+1)
            for thread_index in range(task.threads)
            for request_index in range(task.request_per_thread)
        ]

        for future in futures:
            try:
                logger.info(future.result())
            except Exception as e:
                logger.error(f'Threads Error: {e}', exc_info=True)
