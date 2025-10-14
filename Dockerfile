#####################################################################
# ---------------- STAGE 1 : builder --------------------------------
#####################################################################
FROM python:3.13.4-alpine3.22 AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Минимум для сборки возможных sdist (например, PyYAML на Alpine)
RUN apk add --no-cache --virtual .build-deps \
      build-base \
      python3-dev

WORKDIR /build

# Копируем только метаданные проекта (исходники не нужны)
COPY pyproject.toml README.md ./

# Создаём venv и обновляем pip
RUN python -m venv /venv && \
    /venv/bin/pip install --upgrade pip

# Установим зависимости из pyproject.toml (без сборки приложения)
ARG DEV=false
ENV DEV=${DEV}
RUN python - <<'PY' > /tmp/requirements.txt
import os, tomllib
with open("pyproject.toml", "rb") as f:
    cfg = tomllib.load(f)
proj = cfg.get("project", {}) or {}
deps = list(proj.get("dependencies") or [])
if os.environ.get("DEV","false").lower() == "true":
    dev = (proj.get("optional-dependencies") or {}).get("dev") or []
    deps.extend(dev)
for d in deps:
    print(d)
PY
RUN /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

#####################################################################
# ---------------- STAGE 2 : runtime --------------------------------
#####################################################################
FROM python:3.13.4-alpine3.22

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/scripts:/venv/bin:$PATH"

# Переносим готовое виртуальное окружение
COPY --from=builder /venv /venv

# В рантайме код не копируем — он будет примонтирован томом
WORKDIR /app
COPY scripts /scripts
RUN chmod -R +x /scripts && adduser -D -H -s /sbin/nologin www

USER www
EXPOSE 9000
CMD ["run.sh"]
