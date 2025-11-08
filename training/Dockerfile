# Dockerfile for XGBoost training container
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy training script
COPY train.py /opt/ml/code/train.py

# Set working directory
WORKDIR /opt/ml/code

# Set entrypoint (SageMaker will override this)
ENTRYPOINT ["python", "train.py"]

