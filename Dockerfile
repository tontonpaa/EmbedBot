FROM python:3.12.9-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt --no-cache-dir  # --no-cache-dir を追加
RUN apt-get update && apt-get install -y git

COPY . .

CMD ["python3", "main.py"]