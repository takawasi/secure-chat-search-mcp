FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir '.[postgres]' \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["chat-search", "serve", "--host", "0.0.0.0", "--port", "8000"]
