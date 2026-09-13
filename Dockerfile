FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm \
  && rm -rf /var/lib/apt/lists/*
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY frontend-dashboard ./frontend-dashboard
COPY frontend-overlay ./frontend-overlay
RUN npm install --prefix frontend-dashboard \
  && npm run build --prefix frontend-dashboard \
  && npm install --prefix frontend-overlay \
  && npm run build --prefix frontend-overlay
COPY backend ./backend
ENV PORT=8080
EXPOSE 8080
CMD ["python", "backend/main.py"]
