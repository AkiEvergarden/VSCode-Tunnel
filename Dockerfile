FROM ubuntu:22.04

RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y \
    curl \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN curl -fsSL https://github.com/coder/code-server/releases/download/v4.115.0/code-server_4.115.0_amd64.deb \
    && dpkg -i code-server_4.115.0_amd64.deb \
    && rm code-server_4.115.0_amd64.deb

ENV PATH="/usr/bin/code-server:$PATH"
ENV CODE_SERVER="/usr/bin/code-server"

COPY . /app/

WORKDIR /app
