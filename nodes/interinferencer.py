# == 라이브러리 ==

from state import State
from models.llm import llm
from services.prompt_structure import interinferencer_prompt

# == 전역 변수 ==

# == 필요 함수 ==

# == 노드 함수 ==
def interinferencer(State):
    """
    중간 추론 에이전트
    """
    user_prompt = State.user_prompt
    chat_history = State.get("chat_history", [])
    
    prompt = interinferencer_prompt
    
    try:
        response = (prompt | llm).invoke({"user_prompt": user_prompt, "chat_history": chat_history})
        return {"user_inference": response}
    except Exception as e:
        return {"user_inference": f"interinferencer error: {str(e)}"}