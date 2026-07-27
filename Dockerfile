# Stage 1: Builder
FROM python:3.11-slim AS builder
WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libomp-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ext ./
RUN python3 dezip_setup.py build_ext --inplace

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /downloader

RUN set -eux; \
    CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    echo "deb http://deb.debian.org/debian ${CODENAME} main non-free" > /etc/apt/sources.list.d/non-free.list; \
    echo "deb http://deb.debian.org/debian ${CODENAME}-updates main non-free" >> /etc/apt/sources.list.d/non-free.list; \
    echo "deb http://deb.debian.org/debian-security ${CODENAME}-security main non-free" >> /etc/apt/sources.list.d/non-free.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates unrar megatools; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pre-built extension from builder
COPY --from=builder /build/dezip.* ./

COPY core ./core
COPY download.py reconcile.py app.py scheduler.py ./
RUN chmod +x download.py reconcile.py app.py \
 && ln -s /downloader/reconcile.py /usr/local/bin/reconcile

ENV PYTHONPATH=/downloader

ENTRYPOINT ["python3", "app.py"]