# Stage 1: Builder
FROM python:3.11-slim AS builder
WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libomp-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ext ./
RUN python3 dezip_setup.py build_ext --inplace

# supercronic drives CRON_EXPRESSION-based scheduling in the runtime image (see entrypoint.sh) -
# built here so the runtime stage doesn't need curl just to fetch it.
ENV SUPERCRONIC_VERSION=v0.2.47 \
    SUPERCRONIC_SHA1SUM=712d2ece75da6f6e530192a151488578153e4e96
RUN curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
 && echo "${SUPERCRONIC_SHA1SUM}  supercronic-linux-amd64" | sha1sum -c - \
 && chmod +x supercronic-linux-amd64

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /downloader

RUN set -eux; \
    CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    echo "deb http://deb.debian.org/debian ${CODENAME} main non-free" > /etc/apt/sources.list.d/non-free.list; \
    echo "deb http://deb.debian.org/debian ${CODENAME}-updates main non-free" >> /etc/apt/sources.list.d/non-free.list; \
    echo "deb http://deb.debian.org/debian-security ${CODENAME}-security main non-free" >> /etc/apt/sources.list.d/non-free.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates unrar; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pre-built extension from builder
COPY --from=builder /build/dezip.* ./
COPY --from=builder /build/supercronic-linux-amd64 /usr/local/bin/supercronic

COPY core ./core
COPY main.py ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/downloader

ENTRYPOINT ["./entrypoint.sh"]