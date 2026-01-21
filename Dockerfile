FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Clone and build bgutil POT provider
RUN git clone --single-branch --branch 1.2.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /app/bgutil-provider \
    && cd /app/bgutil-provider/server \
    && npm install \
    && npx tsc

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the bgutil plugin for yt-dlp
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider

# Copy application code
COPY app.py .
COPY start.sh .
RUN chmod +x start.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Start both the POT provider server and the Flask app
CMD ["./start.sh"]
