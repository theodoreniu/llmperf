
import os
import streamlit as st
from dotenv import load_dotenv
from typing import List
from helper import create_db, get_mysql_session, time_now
from tables import create_tables, init_user
from tables import Chunks
from tables import Tasks
from users import current_user, get_authenticator, is_admin, register_user
from report import task_report
from task_form import task_form
from task_loads import find_request, load_all_chunks, load_all_requests, load_all_tasks
import pandas as pd
from sqlalchemy.orm.session import Session

from logger import logger


load_dotenv()


def create_task(session: Session):
    st.markdown("### Create Task")

    task = Tasks(
        status=1,
        enable_think=True,
        created_at=time_now(),
        content_length=2048,
        temperature=0.8,
        timeout=100000,
        threads=1,
        request_per_thread=1,
    )

    task_form(task, session, False)


def render_list(session: Session):

    tasks: List[Tasks] = load_all_tasks(session)
    st.session_state.tasks = tasks

    st.markdown(f"### Tasks ({len(st.session_state.tasks)})")

    if st.button(f"Refresh", key="refresh", icon="🔄"):
        st.session_state.tasks = load_all_tasks(session)

    for task in st.session_state.tasks:

        st.markdown(
            f'{task.status_icon} {task.name} `{task.model_id}` <a href="/?task_id={task.id}" target="_blank">⚙️ Manage</a>',
            unsafe_allow_html=True
        )


def home_page(session: Session):

    task_id = st.query_params.get("task_id", None)
    if task_id:
        return task_page(session, task_id)

    request_id = st.query_params.get("request_id", None)
    if request_id:
        return request_page(session, request_id)

    st.markdown("-----------")

    create_task(session)

    render_list(session)


def request_page(session: Session, request_id: str):
    request = find_request(session, request_id)
    if not request:
        st.error("request not found")
        return

    st.markdown("------")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"id: `{request.id}`")
    with col2:
        st.markdown(f"task_id: `{request.task_id}`")
    with col3:
        st.markdown(f"completed_at: `{request.completed_at}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"input_token_count: `{request.input_token_count}`")
    with col2:
        st.markdown(f"output_token_count: `{request.output_token_count}`")
    with col3:
        st.markdown(f"chunks_count: `{request.chunks_count}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            f"first_token_latency_ms: `{request.first_token_latency_ms}`")
    with col2:
        st.markdown(
            f"last_token_latency_ms: `{request.last_token_latency_ms}`")
    with col3:
        st.markdown(f"request_latency_ms: `{request.request_latency_ms}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"thread_num: `{request.thread_num}`")
    with col2:
        st.markdown(f"request_index: `{request.request_index}`")
    with col3:
        st.markdown(f"success: `{request.success}`")

    st.text_area(label="response: ", value=request.response, height=250)

    render_chunks(session, request, '🚀 Chunks')


def task_page(session: Session, task_id: int):
    st.markdown("-----------")
    st.session_state.task = task = session.query(
        Tasks
    ).filter(
        Tasks.id == task_id
    ).first()

    task = st.session_state.task

    if task.status > 1:
        st.progress(task.progress_percentage)

    if not task:
        st.error("task not found")
        return

    st.markdown(
        f"## {task.status_icon} {task.name} `{task.status_text}` `{task.progress_percentage}%`")

    task_form(task, session, True)

    if task.error_message:
        st.error(task.error_message)

    if task.status > 1:

        with st.spinner(text="Loading Report..."):
            try:
                start_time = time_now()
                data = task_report(session, task)
                end_time = time_now()
                cost_time = round(end_time-start_time, 2)
                df = pd.DataFrame.from_dict(data, orient='index')
                st.markdown("## 📊 Report")
                st.text(f"Query {cost_time} ms")
                st.table(df)
            except Exception as e:
                st.error(e)

        with st.spinner(text="Loading Failed Requests..."):
            render_requests(session, task, 0, '❌ Failed Requests')

        with st.spinner(text="Loading Succeed Requests..."):
            render_requests(session, task, 1, '✅ Succeed Requests')


def render_requests(session: Session, task, status, title):
    try:
        start_time = time_now()
        requests = load_all_requests(session, task, status)
        end_time = time_now()
        cost_time = round(end_time-start_time, 2)
        count = len(requests)
        if count > 0:
            st.markdown(f"## {title} ({count})")
            st.text(f"Query {cost_time} ms")

            with st.container(
                border=True, height=400
            ):
                for request in requests:
                    st.markdown(
                        f'`{request.start_req_time_fmt}` {request.id} `{request.request_index}/{request.thread_num}` <a href="/?request_id={request.id}" target="_blank">Logs</a>',
                        unsafe_allow_html=True
                    )
    except Exception as e:
        st.error(e)


def render_chunks(session: Session, request: Chunks,  title):
    try:
        start_time = time_now()
        chunks = load_all_chunks(session, request)
        end_time = time_now()
        cost_time = round(end_time-start_time, 2)
        list = []

        for chunk in chunks:
            list.append({
                "id": chunk.id,
                "created_at": chunk.created_at,
                "chunk_index": chunk.chunk_index,
                "chunk_content": chunk.chunk_content,
                "token_len": chunk.token_len,
                "request_latency_ms": chunk.request_latency_ms,
                "last_token_latency_ms": chunk.last_token_latency_ms
            })

        count = len(chunks)
        if count > 0:
            st.markdown(f"## {title} ({count})")
            st.text(f"Query {cost_time} ms")
            st.dataframe(list, use_container_width=True)
    except Exception as e:
        st.error(e)


def page_title():
    page_title = "LLM Perf"
    st.set_page_config(
        page_title=page_title,
        page_icon="avatars/favicon.ico",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.image("avatars/logo.svg", width=100)
    st.title(page_title)


if __name__ == "__main__":

    session = get_mysql_session()

    page_title()

    if not os.path.exists("init.lock"):
        if st.button("Initialize Database", key="init_db"):
            create_db()
            create_tables()
            init_user()
            with open("init.lock", "w") as f:
                f.write("ok")

    else:
        authenticator = get_authenticator(session)

        if st.session_state["authentication_status"]:
            st.write(
                f'Welcome `{st.session_state["name"]}`, `{st.session_state["email"]}`')
            col1, col2 = st.columns([10, 2])
            with col1:
                authenticator.logout()

            home_page(session)
        else:
            col1, col2 = st.columns(2)
            with col1:

                authenticator.login(
                    fields={
                        'Form name': 'Login',
                        'Username': 'Alias',
                        'Password': 'Password',
                        'Login': 'Login',
                    },
                )

                if st.session_state["authentication_status"] is False:
                    st.error("Alias/Password is incorrect")

            with col2:
                register_user(session)
