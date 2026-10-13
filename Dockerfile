# Stage 1: build the Angular + NgRx client
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx ng build --base-href /ng/

# Stage 2: Flask API + both dashboards (legacy at /, Angular at /ng/)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=web /web/dist/web/browser ./web/dist/web/browser
RUN python seed.py && useradd -m app && chown -R app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "app:app", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:8000"]
