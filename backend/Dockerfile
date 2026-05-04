FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PDF processing, OCR, and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgomp1 \
    tesseract-ocr \
    libtesseract-dev \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Use root context paths
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt alembic asyncpg

# Copy the backend code and database migrations
COPY backend /app
COPY database /app/database

# Expose the port FastAPI will run on
EXPOSE 8000

# Entry point is handled in docker-compose command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
