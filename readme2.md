# PDF 데이터 추출 프로젝트

## 1. 개요

이 프로젝트는 PDF 파일에서 텍스트, 테이블, 이미지 등의 데이터를 정확하고 효율적으로 추출하기 위해 설계되었습니다. 특히 스캔된 이미지가 아닌, 텍스트 정보가 내장된 **디지털 PDF**에서 최고의 성능을 보입니다.

핵심 기술로 `PyMuPDF` 라이브러리를 사용하여 PDF를 이미지로 변환하는 OCR 과정 없이, 문서 내부의 텍스트, 표, 이미지 객체를 직접 파싱합니다. 이 방식은 기존 OCR 방식에 비해 월등히 빠르고 100%에 가까운 정확도를 제공합니다.

## 2. 주요 기능

- **텍스트 추출**: PDF의 모든 텍스트를 블록 단위로 위치 정보(`bbox`)와 함께 추출합니다.
- **테이블 추출**: PDF 내의 표를 자동으로 감지하여 구조화된 데이터로 변환하고, 각각 `HTML`과 `CSV` 파일로 저장합니다.
- **이미지 추출**: PDF에 포함된 이미지를 손실 없이 원본 형식 그대로 추출하여 파일로 저장합니다.
- **통합 결과 요약**: 추출된 모든 텍스트, 테이블, 이미지에 대한 정보(내용, 위치, 파일 경로 등)를 페이지별로 정리하여 하나의 `summary.json` 파일로 제공합니다.

## 3. 프로젝트 구조 설계

프로젝트는 각 기능의 역할을 명확히 분리하여 유지보수가 용이하도록 설계되었습니다.

- **`main.py`**: 프로그램의 진입점(Entry Point)입니다. 사용자가 입력한 명령줄 인수를 파싱하여 `CLI 모드` 또는 `API 서버 모드`로 프로그램을 실행합니다.

- **`services.py`**: 데이터 추출의 핵심 로직을 담당합니다. `PyMuPDF` 라이브러리를 사용하여 PDF 파일을 열고, 페이지를 순회하며 텍스트, 테이블, 이미지를 추출하는 `extract_pdf_data` 함수가 정의되어 있습니다.

- **`routers.py`**: API 서버의 요청을 처리하는 라우터입니다. FastAPI를 기반으로 하며, `/extract` 엔드포인트로 POST 요청을 받으면 `services.py`의 `extract_pdf_data` 함수를 호출하여 결과를 반환합니다.

- **`schemas.py`**: 프로젝트에서 사용하는 데이터의 형식을 Pydantic 모델로 정의합니다. `TextSpan`, `TableMeta`, `PageResult` 등 데이터 구조를 명확히 하여 안정성을 높입니다.

- **`etc.py`**: 로깅 설정, 출력 디렉토리 생성, JSON 파일 저장 등 프로젝트 전반에서 사용되는 보조 유틸리티 함수들을 포함합니다.

- **`requirements.txt`**: 프로젝트 실행에 필요한 모든 파이썬 라이브러리와 그 버전 정보가 명시되어 있습니다.

## 4. 설치 방법

프로젝트 실행에 필요한 라이브러리들을 아래 명령어를 통해 설치합니다.

```bash
pip install -r requirements.txt
```

## 5. 실행 방법

이 프로젝트는 CLI와 API, 두 가지 방식으로 실행할 수 있습니다.

### 1) CLI (명령줄 인터페이스) 모드

터미널에서 직접 명령어를 입력하여 특정 PDF 파일을 처리하는 방식입니다.

- **명령어 형식**:

  ```bash
  python main.py --pdf "<처리할 PDF 파일 경로>" --out "<결과를 저장할 디렉토리>"
  ```

- **실행 예시**:

  ```bash
  python C:\python_project\Real_project\ocr_ppstructure_project\main.py --pdf "C:\python_project\Real_project\ocr_ppstructure_project\pdf\20251031_company_351514000.pdf" --out "C:\python_project\Real_project\ocr_ppstructure_project\DOCS"
  ```

### 2) API 서버 모드

FastAPI를 사용하여 웹 서버를 실행하고, HTTP 요청을 통해 데이터 추출 기능을 이용하는 방식입니다.

1.  **API 서버 시작**:

    아래 명령어를 실행하여 API 서버를 시작합니다. 기본적으로 8000번 포트에서 실행됩니다.

    ```bash
    python main.py --api
    ```

2.  **데이터 추출 요청**:

    서버가 실행된 후, `http://localhost:8000/v1/extract` 주소로 `POST` 요청을 보내 PDF 처리를 요청할 수 있습니다.

    -   **요청 형식 (JSON)**:
        ```json
        {
          "pdf_path": "<처리할 PDF 파일 경로>",
          "output_root": "<결과를 저장할 디렉토리>"
        }
        ```

    -   **`curl`을 사용한 요청 예시**:
        ```bash
        curl -X POST -H "Content-Type: application/json" -d "{\"pdf_path\": \"C:\\python_project\\Real_project\\ocr_ppstructure_project\\pdf\\20251031_company_351514000.pdf\", \"output_root\": \"C:\\python_project\\Real_project\\ocr_ppstructure_project\\DOCS\"}" http://localhost:8000/v1/extract
        ```
