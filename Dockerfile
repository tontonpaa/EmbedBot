FROM python:3.12.9-slim-bookworm

WORKDIR /app

# Python関連のインストール
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt --no-cache-dir

# ChromeとChromeDriverのためのシステムパッケージ
RUN apt-get update && apt-get install -y \
    git \
    chromium \
    chromium-driver \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# 環境変数など必要に応じて
ENV CHROME_BIN="/usr/bin/chromium"
ENV PATH="$PATH:/usr/lib/chromium/"

COPY . .

CMD ["python3", "main.py"]
