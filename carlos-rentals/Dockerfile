FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY carlos ./carlos
RUN pip install --no-cache-dir .
VOLUME ["/app/data"]
EXPOSE 8080 8081
CMD ["carlos", "run"]
