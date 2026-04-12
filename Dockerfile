# ==============================================================================
# Dockerfile for the OpenRA-RL game server
#
# Builds a working Docker image with the AI opponent fix (slot_bot/spectate).
# The published image (ghcr.io/yxc20089/openra-rl:latest) is broken — see
# README.md "Bugs Found & Fixed" for details.
#
# Usage:
#   docker build -t openra-rl:local .
#   docker run -d -p 8000:8000 --name openra-rl-server -e BOT_TYPE=hard openra-rl:local
# ==============================================================================

# --- Stage 1: Build OpenRA C# game engine from source ---
FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS openra-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    make git libsdl2-dev libopenal-dev libfreetype-dev liblua5.1-0-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pin to a known-good commit that includes the AI bot fix (slot_bot/spectate)
# but predates the protobuf-incompatible interrupt changes.
ARG OPENRA_REPO=https://github.com/yxc20089/OpenRA.git
ARG OPENRA_BRANCH=bleed
ARG OPENRA_COMMIT=cbe7c9e859
RUN git clone --branch "$OPENRA_BRANCH" "$OPENRA_REPO" /src/openra && \
    cd /src/openra && git checkout "$OPENRA_COMMIT"
WORKDIR /src/openra

RUN find . -name '*.sh' -exec sed -i 's/\r$//' {} + && \
    find . -name '*.sh' -exec chmod +x {} +

ENV SKIP_PROTOC=true
RUN make TARGETPLATFORM=unix-generic CONFIGURATION=Release

RUN test -f bin/OpenRA.dll && \
    test -f bin/OpenRA.Game.dll && \
    test -f bin/OpenRA.Mods.Common.dll && \
    test -f bin/OpenRA.Platforms.Null.dll

# --- Stage 2: Install Python server dependencies ---
FROM python:3.11-slim-bookworm AS python-build

ARG OPENRA_RL_REPO=https://github.com/yxc20089/OpenRA-RL.git
ARG OPENRA_RL_BRANCH=main

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth=1 --branch "$OPENRA_RL_BRANCH" "$OPENRA_RL_REPO" /src/openra-rl
WORKDIR /src/openra-rl

RUN pip install --upgrade pip && \
    pip install --no-cache-dir .

# --- Stage 3: Runtime image ---
FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim AS dotnet-runtime

FROM python:3.11-slim-bookworm

LABEL maintainer="OpenRA-RL"
LABEL description="OpenRA RL Environment with AI opponent fix"

COPY --from=dotnet-runtime /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb libgl1-mesa-dri libgl1-mesa-glx libegl-mesa0 \
    mesa-vulkan-drivers libvulkan1 libsdl2-2.0-0 libopenal1 \
    libfreetype6 liblua5.1-0 libicu72 curl procps \
    x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-build /usr/local/bin /usr/local/bin

COPY --from=openra-build /src/openra/bin /opt/openra/bin
COPY --from=openra-build /src/openra/mods /opt/openra/mods
COPY --from=openra-build /src/openra/glsl /opt/openra/glsl
COPY --from=openra-build ["/src/openra/global mix database.dat", "/opt/openra/global mix database.dat"]

RUN LIBDIR=$( [ "$(dpkg --print-architecture)" = "arm64" ] && echo "/usr/lib/aarch64-linux-gnu" || echo "/usr/lib/x86_64-linux-gnu" ) && \
    ln -sf "$LIBDIR/libSDL2-2.0.so.0" /opt/openra/bin/SDL2.so && \
    ln -sf "$LIBDIR/libopenal.so.1" /opt/openra/bin/soft_oal.so && \
    ln -sf "$LIBDIR/libfreetype.so.6" /opt/openra/bin/freetype6.so && \
    ln -sf "$LIBDIR/liblua5.1.so.0" /opt/openra/bin/lua51.so

COPY --from=python-build /src/openra-rl/openra_env/ /app/openra_env/
COPY --from=python-build /src/openra-rl/proto/ /app/proto/
COPY --from=python-build /src/openra-rl/pyproject.toml /app/

COPY --from=python-build /src/openra-rl/docker/entrypoint.sh /entrypoint.sh
COPY --from=python-build /src/openra-rl/docker/replay-viewer.sh /replay-viewer.sh
RUN sed -i 's/\r$//' /entrypoint.sh /replay-viewer.sh && \
    chmod +x /entrypoint.sh /replay-viewer.sh

RUN mkdir -p /root/.config/openra/Content/ra/v2/expand /root/.config/openra/Content/ra/v2/cnc && \
    ( curl -sfL --max-time 30 -o /tmp/ra-quickinstall.zip \
        https://openra.baxxster.no/openra/ra-quickinstall.zip && \
    apt-get update && apt-get install -y --no-install-recommends unzip && \
    unzip -o /tmp/ra-quickinstall.zip -d /tmp/ra-content && \
    cp /tmp/ra-content/*.mix /root/.config/openra/Content/ra/v2/ && \
    cp /tmp/ra-content/expand/* /root/.config/openra/Content/ra/v2/expand/ && \
    cp /tmp/ra-content/cnc/* /root/.config/openra/Content/ra/v2/cnc/ && \
    rm -rf /tmp/ra-quickinstall.zip /tmp/ra-content && \
    apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    ) || echo "WARNING: RA content download failed (replay viewer will be unavailable)"

ENV OPENRA_PATH=/opt/openra
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_ROLL_FORWARD=LatestMajor
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV MESA_GL_VERSION_OVERRIDE=3.3
ENV AI_SLOT=Multi0
ENV BOT_TYPE=beginner
ENV RECORD_REPLAYS=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "openra_env.server.app"]
