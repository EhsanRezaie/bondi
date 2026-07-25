FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["sh", "entrypoint.sh"]
