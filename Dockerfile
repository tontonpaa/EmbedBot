FROM python:3.7-slim-buster

WORKDIR /app

RUN ls -la /  # ルートディレクトリの内容を確認
COPY requirements.txt /
RUN ls -la /  # ルートディレクトリの内容を再度確認
RUN pip install -r requirements.txt

COPY . .

CMD ["python3", "main.py"]