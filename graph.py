# == 라이브러리 ==
from langgraph.graph import END, StateGraph
from state import State

# == Agent import ==
from nodes.answersynthesizer import answersynthesizer
from nodes.datareader_node import datareader_node
from nodes.graphdrawer_node import graphdrawer_node
from nodes.interinferencer import interinferencer
from nodes.questiongenerator import questiongenerator
from nodes.toolrouter import toolrouter
from state import State

# == 함수 정의 ==

def pass_tools_if_first_graph(State):
    """ 
    초반 분기 처리 함수 
    """
    if State.first_graph:
        return "skip"
    else:
        return "nonskip"

def tools_condition(State):
    """
    도구 분기 처리 함수
    """
    tool_use = State.tool_use
    return tool_use



# == 그래프 구조 ==
def workflow_function(State):
    # == state 정의 ==
    workflow = StateGraph(State)

    # == 노드 등록 ==
    workflow.add_node("QuestionGenerator", questiongenerator)
    workflow.add_node("ToolRouter", toolrouter)
    workflow.add_node("datareader_node", datareader_node)
    workflow.add_node("graphdrawer_node", graphdrawer_node)
    workflow.add_node("InterInferencer", interinferencer)
    workflow.add_node("AnswerSynthesizer", answersynthesizer)
    workflow.add_node(END, None)

    # == 시작점 ==
    workflow.set_start("InterInferencer")

    # == 분기 ==
    workflow.add_conditional_edges("InterInferencer", pass_tools_if_first_graph, {
        "skip" : "QuestionGenerator",
        "nonskip":"toolRouter"
    })

    # == 노드 연결 == 
    workflow.add_edge("InterInferencer","ToolRouter")
    

    # == 툴 연결 ==
    workflow.add_conditional_edges("ToolRouter",tools_condition,
                                    {
        "datareader" : "datareader_node",
        "graphdrawer" : "graphdrawer_node",
        "none" : "AnswerSynthesizer",},
    )
    
    workflow.add_edge("datareader_node", "AnswerSynthesizer")
    workflow.add_edge("graphdrawer_node", "AnswerSynthesizer")
    workflow.add_edge("AnswerSynthesizer","QuestionGenerator")

    # == 완료 == 
    workflow.add_edge("QuestionGenerator",END)