FROM python:3.11-slim

# Install ffmpeg (needed for audio conversion)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN pip install yt-dlp google-cloud-storage flask gunicorn

WORKDIR /app
COPY app.py .

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
