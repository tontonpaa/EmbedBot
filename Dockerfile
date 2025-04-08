FROM python:3.12-slim-buster

WORKDIR /app

ADD . /app/
RUN ls -la /app/
COPY ./requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN ls -la /app/
RUN pip install -r requirements.txt

COPY . .

CMD ["python3", "main.py"]