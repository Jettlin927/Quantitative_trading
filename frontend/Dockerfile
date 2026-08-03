FROM node:22-alpine AS frontend-build

WORKDIR /app

ARG NPM_CONFIG_REGISTRY=

COPY frontend/package*.json ./
RUN if [ -n "$NPM_CONFIG_REGISTRY" ]; then \
      npm config set registry "$NPM_CONFIG_REGISTRY"; \
    fi && npm ci --include=optional

COPY frontend/ ./
RUN npm run typecheck \
    && npm run lint \
    && npm test -- --run \
    && npm run build

FROM nginx:stable-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 frontend/docker-entrypoint.d/15-personal-gateway.sh /docker-entrypoint.d/15-personal-gateway.sh
COPY --from=frontend-build /app/dist/ /usr/share/nginx/html/

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD wget -q -O /dev/null http://127.0.0.1:5173/healthz || exit 1
