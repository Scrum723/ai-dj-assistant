FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm \
  && rm -rf /var/lib/apt/lists/*
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY frontend-dashboard ./frontend-dashboard
COPY backend ./backend
RUN npm install --prefix frontend-dashboard \
  && npm run build --prefix frontend-dashboard \
  && if [ -d backend/frontend-overlay ]; then \
       npm install --prefix backend/frontend-overlay \
       && npm run build --prefix backend/frontend-overlay \
       && mkdir -p frontend-overlay \
       && cp -R backend/frontend-overlay/dist frontend-overlay/dist; \
     fi
ENV PORT=8080
EXPOSE 8080
CMD ["python", "backend/main.py"]
