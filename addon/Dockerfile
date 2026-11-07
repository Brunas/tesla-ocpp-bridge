FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .

# /data holds options.json (Home Assistant) and state.json (open transaction, queue)
VOLUME ["/data"]

CMD ["python", "-m", "tesla_ocpp_bridge"]
