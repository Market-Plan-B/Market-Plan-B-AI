

from app.models.llm import llm
from app.services.prompt_structure_korean import actiongenerator_prompt
from app.services.datafram_save import df, news_json


# == 변수 ==


# == 필요 함수 ==


# == 대응책 작성 함수 ==
def actiongenerator(date,structured_data, news_items, model_prediction, xai_result, precomputed_strategies, unstructured_data):
    """
    대응책 생성 에이전트
    """

    prompt = actiongenerator_prompt
    
    try:
        response = (prompt | llm).invoke({"precomputed_strategies":precomputed_strategies,"report_date": date, "model_prediction": model_prediction,"xai_result": xai_result,"structured_data": structured_data, "news_items" : unstructured_data})
        return response
    
    except Exception as e:
        return {"actiongenerator": f"actiongenerator error: {str(e)}"}

