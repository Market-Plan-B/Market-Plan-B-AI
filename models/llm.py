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

llm = ChatOpenAI(model="gpt-4o", temperature=0.1, max_tokens=None)

# 플래너용 structured output LLM
llm_with_agent_route = llm.with_structured_output(AgentRoute)