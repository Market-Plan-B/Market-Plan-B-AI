# == 라이브러리 ==

from state import State
from models.llm import llm
from services.prompt_structure import questiongenerator_prompt

# == 전역 변수 ==

# == 필요 함수 ==

# == 노드 함수 ==
def questiongenerator(State):
    """
    추천 질문 생성 에이전트
    """
    user_prompt = State.user_prompt
    user_inference = State.user_inference
    
    prompt = questiongenerator_prompt
    
    try:
        response = (prompt | llm).invoke({"user_prompt": user_prompt, "user_inference" : user_inference})
        return {"generated_questions": response}
    except Exception as e:
        return {"generated_questions": f"questiongenerator error: {str(e)}"}