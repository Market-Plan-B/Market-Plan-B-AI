# == 라이브러리 ==


from state import State
from models.llm import llm
from services.prompt_structure import answersynthesizer_prompt

# == 전역 변수 ==

# == 필요 함수 ==


# == 노드 함수 ==
def answersynthesizer(State):
    """
    답변 생성 에이전트
    """
    user_inference = State.user_inference
    tool_answer = State.tool_answer
    tool_user_bool = State.tool_user_bool
    user_prompt = State.user_prompt
    chat_history = State.get("chat_history", [])
    prompt = answersynthesizer_prompt

    try:
        response = (prompt | llm).invoke({"user_inference": user_inference, "tool_answer":tool_answer, "tool_user_bool":tool_user_bool})
        
        # chat_history에 대화 추가
        new_chat = {"human": user_prompt, "assistant": response}
        updated_history = chat_history + [new_chat]
        
        return {"answer": response, "chat_history": updated_history}
    except Exception as e:
        return {"answer": f"answersynthesizer error: {str(e)}"}

