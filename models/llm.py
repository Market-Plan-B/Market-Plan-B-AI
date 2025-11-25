import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from app.models import AgentRoute

load_dotenv()

# 기본 LLM
llm = ChatOpenAI(
    model=os.getenv('OPENAI_MODEL', 'gpt-4o'),
    temperature=float(os.getenv('TEMPERATURE', '0.0')),
    api_key=os.getenv('OPENAI_API_KEY')
)

# json format으로 뽑는 애
llm_json_format = ChatOpenAI(
    model=os.getenv('OPENAI_MODEL', 'gpt-4o'),
    temperature=float(os.getenv('TEMPERATURE', '0.0')),
    api_key=os.getenv('OPENAI_API_KEY'),
    response_format={"type": "json_object"}
)

# html용으로 text로 뽑는 애
llm_text_format = ChatOpenAI(
    model=os.getenv('OPENAI_MODEL', 'gpt-4o'),
    temperature=float(os.getenv('TEMPERATURE', '0.0')),
    api_key=os.getenv('OPENAI_API_KEY'),
    response_format={"type": "text"}
)

# 플래너용 structured output LLM
llm_with_agent_route = llm.with_structured_output(AgentRoute)
