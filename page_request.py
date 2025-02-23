
import streamlit as st
from dotenv import load_dotenv
from helper import get_mysql_session, time_now
from tables import Chunks, Requests
from task_loads import load_all_chunks

from users import current_user, is_admin

load_dotenv()


def request_page(request_id: str):

    request = None

    session = get_mysql_session()

    if is_admin():
        request = session.query(
            Requests
        ).filter(
            Requests.id == request_id
        ).first()
    else:
        request = session.query(
            Requests
        ).filter(
            Requests.id == request_id,
            Requests.user_id == current_user().id
        ).first()

    session.close()

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

    render_chunks(request, '🚀 Chunks')


def render_chunks(request: Chunks,  title):
    try:
        start_time = time_now()
        chunks = load_all_chunks(request)
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
