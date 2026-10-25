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
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % (__import__('os').environ.get('PORT','8080'))).read()" || exit 1
CMD ["python", "backend/main.py"]
