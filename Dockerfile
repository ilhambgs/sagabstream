# Menggunakan official Playwright image untuk Python yang sudah lengkap dengan Chromium
FROM mcr.microsoft.com/playwright/python:v1.64.1-jammy

WORKDIR /app

# Salin file kebutuhan
COPY requirements.txt .

# Install Flask dan dependensi python lainnya
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh file aplikasi
COPY . .

# Jalankan aplikasi dengan Gunicorn
EXPOSE 10000
CMD ["gunicorn", "app:py", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "2", "--timeout", "120"]
