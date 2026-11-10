FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine/ engine/
COPY server/ server/
COPY presets/ presets/
EXPOSE 8900
CMD ["uvicorn","server.api.main:app","--host","0.0.0.0","--port","8900"]
