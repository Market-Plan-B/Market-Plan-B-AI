import torch

from transformers import AutoTokenizer, AutoModel

# CrudeBERT 모델 로드
def load_crudebert():
    """
    crude bert 모델 함수 로드
    """
    tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
    model = AutoModel.from_pretrained("Captain-1337/CrudeBERT")
    model.eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    return tokenizer, model, device


# =========================
#   임베딩 함수
# =========================

def crudebert_embedding(text):
    """
    
    """

    tokenizer, model, device = load_crudebert()

    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=512
    ).to(device)

    with torch.no_grad():
        outputs = model(**inputs)

    # CLS 토큰 임베딩
    emb = outputs.last_hidden_state[:, 0, :].cpu().numpy()[0]
    return emb 