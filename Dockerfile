FROM node:20-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    python3 \
    python3-pip \
    curl \
    git \
  && pip3 install --no-cache-dir --break-system-packages requests \
  && pip3 install --no-cache-dir --break-system-packages "yt-dlp[default] @ https://github.com/coletdjnz/yt-dlp-dev/archive/refs/heads/feat/youtube/sabr.zip" \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY . .

ENV NODE_ENV=production

CMD ["npm", "start"]
