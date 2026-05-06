FROM node:20-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git jq \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @mariozechner/pi-coding-agent \
 && pi --help >/dev/null 2>&1 || true

WORKDIR /work

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
