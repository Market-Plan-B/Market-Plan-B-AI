import json
from langchain.prompts import PromptTemplate

from app.models.llm import llm_json_format
from app.services.prompt_structure_korean import actiongenerator_prompt


# == 변수 ==


# == 필요 함수 ==


# == 대응책 작성 함수 ==
def actiongenerator(date, structured_data, model_prediction, xai_result, unstructured_data):
    template = PromptTemplate(
        input_variables=actiongenerator_prompt["input_variables"],
        template=actiongenerator_prompt["template"]
    )

    structured_str = structured_data.to_string(index=False)
    model_pred_str = json.dumps(model_prediction, ensure_ascii=False, indent=2)
    xai_str = json.dumps(xai_result, ensure_ascii=False, indent=2)
    news_str = unstructured_data

    final_prompt = template.format(
        role=actiongenerator_prompt["role"],
        rules=actiongenerator_prompt["rules"],
        output_schema=actiongenerator_prompt["output_schema"],
        fewshot=actiongenerator_prompt["fewshot"],

        report_date=date,
        structured_data=structured_str,
        news_items=news_str,
        model_prediction=model_pred_str,
        xai_result=xai_str,
    )

    try:
        response = (template | llm_json_format).invoke({
            "role": actiongenerator_prompt["role"],
            "rules": actiongenerator_prompt["rules"],
            "output_schema": actiongenerator_prompt["output_schema"],
            "fewshot": actiongenerator_prompt["fewshot"],

            "report_date": date,
            "structured_data": structured_str,
            "news_items": news_str,
            "model_prediction": model_pred_str,
            "xai_result": xai_str,
        })


        return response.content

    except Exception as e:
        return f"actiongenerator error: {str(e)}"
