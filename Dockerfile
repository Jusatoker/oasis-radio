FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    mpv \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

RUN mkdir -p /data

EXPOSE 5000

CMD ["gunicorn", "--workers=1", "--bind=0.0.0.0:5000", "--timeout=120", "--access-logfile=-", "server:app"]
