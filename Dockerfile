FROM python:3.7-slim-buster

WORKDIR /app

RUN ls -la /app/  # 追加
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python3", "main.py"]