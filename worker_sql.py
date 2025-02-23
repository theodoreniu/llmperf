from time import sleep
from helper import get_mysql_session, redis_client

from sqlalchemy import update
from serialize import chunk_dequeue, request_dequeue
from tables import Tasks


from logger import logger

if __name__ == "__main__":

    while (True):
        try:
            session = get_mysql_session()
            redis = redis_client()

            chunk = chunk_dequeue(redis)
            if chunk:
                logger.info(chunk.__dict__)
                session.add(chunk)
                session.commit()

            request = request_dequeue(redis)
            if request:
                logger.info(request.__dict__)
                session.add(request)
                session.commit()

                if request.success:
                    session.execute(
                        update(
                            Tasks
                        ).where(
                            Tasks.id == request.task_id
                        ).values(
                            request_succeed=Tasks.request_succeed + 1
                        )
                    )
                else:
                    session.execute(
                        update(
                            Tasks
                        ).where(
                            Tasks.id == request.task_id
                        ).values(
                            request_failed=Tasks.request_failed + 1
                        )
                    )

            session.close()
            redis.close()

            if not chunk and not request:
                logger.info("waitting for sql ...")
                sleep(1)

        except Exception as e:
            session.close()
            logger.error(f'Error: {e}', exc_info=True)
            sleep(1)
