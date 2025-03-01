from helper import redis_client
from tables import Tasks
from task_runtime import TaskRuntime
from theodoretools.bot import feishu_text
from concurrent.futures import ThreadPoolExecutor
from logger import logger
from config import app_url


def safe_create_and_run_task(task: Tasks, thread_num: int, request_index: int, redis):
    task_runtime = TaskRuntime(
        task=task, thread_num=thread_num, request_index=request_index, redis=redis
    )
    task_runtime.latency()


def task_executor(task: Tasks):

    if task.feishu_token:
        feishu_text(
            f"start to run {task.name}: {app_url}/?task_id={task.id}", task.feishu_token
        )

    redis = redis_client()

    try:
        with ThreadPoolExecutor(max_workers=task.threads) as executor:
            futures = [
                executor.submit(
                    safe_create_and_run_task,
                    task,
                    thread_index + 1,
                    request_index + 1,
                    redis,
                )
                for thread_index in range(task.threads)
                for request_index in range(task.request_per_thread)
            ]

            for future in futures:
                try:
                    logger.info(future.result())
                except Exception as e:
                    logger.error(f"Threads Error: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Task Error: {e}", exc_info=True)
        raise e
    finally:
        redis.close()
