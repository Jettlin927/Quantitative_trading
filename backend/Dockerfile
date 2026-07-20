FROM python:3.12-slim

WORKDIR /app

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG APP_GIT_COMMIT=unknown
ARG APP_GIT_REF=unknown

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_GIT_COMMIT=$APP_GIT_COMMIT
ENV APP_BUILD_ID=$APP_GIT_COMMIT
ENV APP_GIT_REF=$APP_GIT_REF

COPY backend/requirements.txt /tmp/requirements.txt
RUN if [ -n "$PIP_INDEX_URL" ] && [ -n "$PIP_TRUSTED_HOST" ]; then \
      pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r /tmp/requirements.txt; \
    elif [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r /tmp/requirements.txt; \
    else \
      pip install --no-cache-dir -r /tmp/requirements.txt; \
    fi

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
