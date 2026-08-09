FROM python:3.11-slim

WORKDIR /app

# Playwright uchun tizim kutubxonalari
RUN apt-get update && apt-get install -y \
    wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright brauzerini o'rnatish (Chromium)
RUN playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/sessions

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
