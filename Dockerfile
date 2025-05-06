FROM python:3.12.9-slim-bookworm

WORKDIR /app

# Python関連のインストール
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt --no-cache-dir

# ChromeとChromeDriverのためのシステムパッケージ
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgcc1 \
    libgdk-pixbuf2.0-0 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    chromium \
    chromium-driver \
    git \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# 環境変数など必要に応じて
ENV CHROME_BIN="/usr/bin/chromium"
ENV PATH="$PATH:/usr/lib/chromium/"

COPY . .

CMD ["python3", "main.py"]
