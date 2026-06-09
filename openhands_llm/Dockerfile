FROM python:3.12-slim

WORKDIR /app

COPY openhands_llm/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY openhands_llm/ openhands_llm/
COPY common/ common/

ENV PYTHONPATH=/app
ENV OPENHANDS_URL=http://localhost:3000
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "openhands_llm.server:app", "--host", "0.0.0.0", "--port", "8000"]
