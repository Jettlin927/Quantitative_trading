FROM node:22-alpine

WORKDIR /app

ARG NPM_CONFIG_REGISTRY=

COPY frontend/package*.json ./
RUN if [ -n "$NPM_CONFIG_REGISTRY" ]; then \
      npm config set registry "$NPM_CONFIG_REGISTRY"; \
    fi && npm install

COPY frontend/ ./

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
