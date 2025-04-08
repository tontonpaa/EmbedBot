FROM python:3.7-slim-buster

WORKDIR /app

COPY html＆CSS置き場/ディスコード埋め込みbot作成記録/requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python3", "main.py"]