# syntax=docker/dockerfile:1
#
# FRT database + admin UI in one container.
#   - MySQL 8.4 (official image, first-start init driven by MYSQL_ROOT_PASSWORD)
#   - Python 3.12 with the FRT parser scripts from the repository root
#   - Node 22 admin UI on port 8080
#
# Build from the repository root (docker-compose.yml does this for you):
#   docker build -f docker/Dockerfile -t frt-db .

# ---- Node runtime and app dependencies -------------------------------------
FROM node:22.12.0-bookworm-slim AS node
WORKDIR /build
COPY docker/app/package.json docker/app/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# ---- Final image ------------------------------------------------------------
FROM mysql:8.4

RUN microdnf install -y python3.12 python3.12-pip shadow-utils \
 && microdnf clean all \
 && useradd --system --create-home --home-dir /home/frt --shell /sbin/nologin frt

COPY docker/requirements.txt /tmp/requirements.txt
RUN python3.12 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# The official Node binaries need only glibc >= 2.28 and libstdc++, both present here.
COPY --from=node /usr/local/bin/node /usr/local/bin/node

# FRT pipeline scripts (repository root) plus the swap helper
COPY download_frt.py parse_frt.py split_frn.py createDb.py importIntoDb.py /app/frt/
COPY docker/scripts/swap_db.py /app/frt/

# Admin UI
COPY --from=node /build/node_modules /app/ui/node_modules
COPY docker/app/package.json docker/app/server.js /app/ui/
COPY docker/app/lib /app/ui/lib
COPY docker/app/public /app/ui/public

COPY docker/mysql/frt.cnf /etc/mysql/conf.d/frt.cnf
COPY docker/entrypoint.sh /usr/local/bin/frt-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/frt-entrypoint.sh \
 && chmod 0755 /usr/local/bin/frt-entrypoint.sh \
 && chmod 0644 /etc/mysql/conf.d/frt.cnf \
 && mkdir -p /data && chown frt:frt /data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    DATA_DIR=/data \
    FRT_SCRIPTS_DIR=/app/frt \
    PYTHON=/opt/venv/bin/python

VOLUME ["/data"]
EXPOSE 8080 3306

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/frt-entrypoint.sh"]
