# syntax=docker/dockerfile:1
# Agent-Based ShippingService  Ollama / Llama 3 variant

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY shippingservice/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Generate protobuf files as root while we still can write
COPY shippingservice/proto/demo.proto ./proto/
RUN python -m grpc_tools.protoc \
    -I./proto \
    --python_out=. \
    --grpc_python_out=. \
    ./proto/demo.proto

# Copy application files
COPY shippingservice/main.py shippingservice/orchestrator.py ./
COPY shippingservice/repository.py shippingservice/database.py shippingservice/config.py shippingservice/fault_injection.py ./
COPY shippingservice/agents/ ./agents/
COPY shippingagent/app/ /shippingagent/app/
COPY boundary_validation.py /boundary_validation.py

# Create non-root user, fix ownership of everything in /app in one layer
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app \
    && chmod -R 755 /app

USER appuser

ENV PORT=50051
ENV LLAMA_BASE_URL=http://ollama.pauric-test.svc.cluster.local:11434/v1
ENV LLAMA_MODEL=llama3:latest

EXPOSE 50051

CMD ["python", "main.py"]
