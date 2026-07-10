# 1) 프론트 빌드
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install
COPY web/ ./
RUN npm run build

# 2) 백엔드 런타임
FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --from=web /web/dist ./web/dist
ENV HOST=0.0.0.0 PORT=8080 DATA_DIR=/srv/data
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
