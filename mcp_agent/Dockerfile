FROM python:3.12-slim

WORKDIR /app

COPY mcp_agent/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_agent/ mcp_agent/
COPY prompts/ prompts/
COPY config/ config/

ENV OPENHANDS_URL=http://openhands_llm:8000
ENV PORT=8000

EXPOSE 8000

CMD ["python", "-m", "mcp_agent.server"]
