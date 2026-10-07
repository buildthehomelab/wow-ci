# ghcr.io/buildthehomelab/wow-ci-tools: the toolchain the cpp and addon jobs run in, so no
# job installs packages (apt mirrors on hosted runners hang now and then).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git python3 \
      clang-18 libboost-dev libmysqlclient-dev libssl-dev \
      lua5.1 lua-check \
 && ln -s /usr/bin/clang++-18 /usr/local/bin/clang++ \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
LABEL org.opencontainers.image.source=https://github.com/buildthehomelab/wow-ci
LABEL org.opencontainers.image.description="Toolchain for wow-ci (clang 18, Boost/MySQL/OpenSSL headers, Lua 5.1, luacheck)"
