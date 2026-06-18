FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY app/ /app
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/uploads/thumbnails
EXPOSE 8083
CMD ["python", "app.py"]