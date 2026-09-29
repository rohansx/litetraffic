FROM python:3.13-slim

ARG TARGETARCH=amd64
ARG K6_VERSION=v2.2.0
# SHA-256 values from the official k6 v2.2.0 release checksums file.
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sum=b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d ;; \
      arm64) sum=4ecd64cadcc792402d16293836115480419c4447c032858f564852d98f1bf54c ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    name="k6-$K6_VERSION-linux-$TARGETARCH"; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/k6.tar.gz')" \
      "https://github.com/grafana/k6/releases/download/$K6_VERSION/$name.tar.gz"; \
    echo "$sum  /tmp/k6.tar.gz" | sha256sum --check -; \
    tar -xzf /tmp/k6.tar.gz -C /tmp; \
    install -m 755 "/tmp/$name/k6" /usr/local/bin/k6; \
    rm -rf /tmp/k6.tar.gz "/tmp/$name"

COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN pip install --no-cache-dir '/src[ai]' && rm -rf /src

# /work is owned by the user so `doctor`/`verify` can write .litetraffic/runs without a mount.
RUN useradd --create-home --uid 1000 litetraffic && install -d -o litetraffic /work
USER litetraffic
WORKDIR /work
ENTRYPOINT ["litetraffic"]
CMD ["--help"]
