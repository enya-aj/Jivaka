FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# libgl1/libglib2.0-0: required by opencv (an EasyOCR dependency) at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==1.8.3"

WORKDIR /app/backend

COPY pyproject.toml ./
RUN poetry install --no-root --only main

# ThaiNER model for patient record de-identification, baked into the image
# so ingestion never depends on a download at runtime. The catalog URL is
# PyThaiNLP's GitHub copy, which some networks reach when pythainlp.org isn't.
ENV PYTHAINLP_DATA=/opt/pythainlp-data
RUN python -c "from pythainlp.corpus import download; \
assert download('thainer-1.4', url='https://raw.githubusercontent.com/PyThaiNLP/pythainlp-corpus/master/db.json')"

COPY src ./src
RUN poetry install --only main

EXPOSE 8000

CMD ["uvicorn", "jivaka.main:app", "--host", "0.0.0.0", "--port", "8000"]
