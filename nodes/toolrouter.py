# == 라이브러리 ==

from state import State
from models.llm import llm
from services.prompt_structure import toolrouter_prompt

# == 전역 변수 ==

# == 필요 함수 ==

# == 노드 함수 ==
def toolrouter(State):
    """
    도구 라우팅 에이전트
    """
    user_prompt = State.user_prompt
    tool_available = State.tool_available
    
    prompt = toolrouter_prompt
    
    try:
        response = (prompt | llm).invoke({"user_prompt": user_prompt, "available_tools": tool_available})
        return {"selected_tool": response}
    except Exception as e:
        return {"selected_tool": f"toolrouter error: {str(e)}"}