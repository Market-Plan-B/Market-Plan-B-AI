# == 라이브러리 ==
from typing import Any,TypedDict, Annotated, Literal
from langgraph.graph import add_messages

from models.llm import llm


# == 변수 정의 ==
max_chat_history = 5
delete_history = 5

# == message 정의 ==
def add_message_limit(history: list[Any], new_message: Any) -> list[Any]:
    if len(history) > max_chat_history:
        # 그냥 초반 부터 n개 삭제 
        for i in range(0,delete_history-1):
            del history[0]
    else:
        history = add_messages(history, new_message)
    return history

# == 라이브러리 ==
class State(TypedDict):
    daily_report : Annotated[str, "어제에 있었던 리포트"]
    user_prompt : Annotated[str, " 사용자의 현재 질문 "]
    tool_use : Annotated[str, "사용할 도구 이름, 없으면 none"]
    tool_query : Annotated[dict, "사용할 도구 넣을 변수들"]
    tool_answer : Annotated[Any,"사용한 도구의 결과값"]
    tool_available : Annotated[list,"사용 가능한 도구 저장"]
    tool_user_bool : Annotated[bool, "도구가 유저 요청인지 판단 true면 유저 요청"]
    answer : Annotated[str, "생성한 답변"]
    recommand_question : Annotated[list[str], "추천할 답변"]
    first_graph : Annotated[bool, "처음 그래프 시작 시 바로 recommand_question으로 시작"]
    user_inference : Annotated[str, "유저 목적,의도 추론"]
    chat_history : Annotated[list[dict], "유저 대화 저장", add_message_limit]
    