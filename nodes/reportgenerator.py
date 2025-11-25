from app.models.llm import llm_text_format
from app.services.prompt_structure_korean import reportgenerator_prompt
from app.services.prompt_structure_korean import reportgenerator_prompt

from langchain_core.prompts import PromptTemplate

import json

# == 변수 ==


# == 필요 함수 ==

# == 보고서 작성 함수 ==
def reportgenerator(
    date,
    structured_data,
    model_prediction,
    xai_result,
    precomputed_strategies,
    unstructured_data
):

    template = PromptTemplate(
        input_variables=reportgenerator_prompt["input_variables"],
        template=reportgenerator_prompt["template"]
    )

    if hasattr(structured_data, "to_string"):
        structured_str = structured_data.to_string(index=False)
    else:
        structured_str = str(structured_data)

    model_pred_str = json.dumps(model_prediction, ensure_ascii=False, indent=2)
    xai_str = json.dumps(xai_result, ensure_ascii=False, indent=2)
    strategies_str = json.dumps(precomputed_strategies, ensure_ascii=False, indent=2)

    news_str = unstructured_data  

    final_prompt = template.format(
        role=reportgenerator_prompt["role"],
        rules=reportgenerator_prompt["rules"],
        output_schema=reportgenerator_prompt["output_schema"],
        fewshot=reportgenerator_prompt["fewshot"],

        report_date=date,
        structured_data=structured_str,
        news_items=news_str,
        model_prediction=model_pred_str,
        xai_result=xai_str,
        precomputed_strategies=strategies_str
    )
    print(final_prompt)


    try:
        response = (template | llm_text_format).invoke({
            "role": reportgenerator_prompt["role"],
            "rules": reportgenerator_prompt["rules"],
            "output_schema": reportgenerator_prompt["output_schema"],
            "fewshot": reportgenerator_prompt["fewshot"],

            "report_date": date,
            "structured_data": structured_str,
            "news_items": news_str,
            "model_prediction": model_pred_str,
            "xai_result": xai_str,
            "precomputed_strategies": strategies_str
        })

        return response.content

    except Exception as e:
        return f"reportgenerator error: {str(e)}"
