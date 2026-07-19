# Build019.8 - Build KataGo Eigen CPU engine and download small model

FROM python:3.11-slim AS katago-builder

RUN apt-get update && apt-get install -y --no-install-recommends \

    git \

    cmake \

    make \

    g++ \

    libeigen3-dev \

    zlib1g-dev \

    libzip-dev \

    ca-certificates \

    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN git clone --depth 1 --branch v1.16.5 \

    https://github.com/lightvector/KataGo.git

WORKDIR /build/KataGo/cpp

RUN cmake . \

    -DUSE_BACKEND=EIGEN \

    -DNO_GIT_REVISION=1 \

    -DNO_LIBZIP=1 \

    && make -j2

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \

    curl \

    ca-certificates \

    zlib1g \
    libzip5 \
    libstdc++6 \

    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/bin /app/models /app/config

COPY --from=katago-builder \

    /build/KataGo/cpp/katago \

    /app/bin/katago

RUN chmod +x /app/bin/katago

RUN curl -L --fail --retry 3 \

     "https://media.katagotraining.org/uploaded/networks/models/kata1/kata1-b6c96-s65341184-d9428755.txt.gz" \

    ENV KATAGO_MODEL=/app/models/model.txt.gz

ENV KATAGO_BIN=/app/bin/katago

ENV KATAGO_MODEL=/app/models/model.bin.gz

ENV KATAGO_CONFIG=/app/config/analysis.cfg

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
