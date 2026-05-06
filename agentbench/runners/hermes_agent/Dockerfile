FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git curl jq build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt
RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent || \
    git clone --depth 1 https://github.com/nousresearch/hermes-agent.git hermes-agent

WORKDIR /opt/hermes-agent
RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install --no-cache-dir -e ".[all]" || \
    uv pip install --no-cache-dir -e .

ENV PATH="/opt/hermes-agent/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
