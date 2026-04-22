# ==============================================================================
# Dockerfile for the OpenRA-RL game server
#
# Builds a working Docker image with the AI opponent fix (slot_bot/spectate).
# The published image (ghcr.io/yxc20089/openra-rl:latest) is broken — see
# README.md "Bugs Found & Fixed" for details.
#
# Usage:
#   docker build -t openra-rl:local .
#   docker run -d -p 8000:8000 --name openra-rl-server -e BOT_TYPE=easy openra-rl:local
# ==============================================================================

# --- Stage 1: Build OpenRA C# game engine from source ---
FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS openra-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    make git libsdl2-dev libopenal-dev libfreetype-dev liblua5.1-0-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pin to a known-good OpenRA revision with the RL bridge placement fallback fix.
ARG OPENRA_REPO=https://github.com/yxc20089/OpenRA.git
ARG OPENRA_BRANCH=bleed
ARG OPENRA_COMMIT=8a5d224223e0498e006a7350a9767a87bd45a708
RUN git clone --branch "$OPENRA_BRANCH" "$OPENRA_REPO" /src/openra && \
    cd /src/openra && git checkout "$OPENRA_COMMIT"
WORKDIR /src/openra

RUN find . -name '*.sh' -exec sed -i 's/\r$//' {} + && \
    find . -name '*.sh' -exec chmod +x {} +

RUN make TARGETPLATFORM=unix-generic CONFIGURATION=Release

RUN test -f bin/OpenRA.dll && \
    test -f bin/OpenRA.Game.dll && \
    test -f bin/OpenRA.Mods.Common.dll && \
    test -f bin/OpenRA.Platforms.Null.dll

# --- Stage 2: Install Python server dependencies ---
FROM python:3.11-slim-bookworm AS python-build

ARG OPENRA_RL_REPO=https://github.com/yxc20089/OpenRA-RL.git
ARG OPENRA_RL_COMMIT=5dadd449c912ac2d4021cc8ed84fc0b385b1543c

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone "$OPENRA_RL_REPO" /src/openra-rl && \
    cd /src/openra-rl && git checkout "$OPENRA_RL_COMMIT"
WORKDIR /src/openra-rl

RUN pip install --upgrade pip && \
    pip install --no-cache-dir .

# --- Stage 3: Runtime image ---
FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim AS dotnet-runtime

FROM python:3.11-slim-bookworm

LABEL maintainer="OpenRA-RL"
LABEL description="OpenRA RL Environment with AI opponent fix"

RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    XDG_CONFIG_HOME=/home/user/.config
WORKDIR $HOME/app

COPY --from=dotnet-runtime /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb libgl1-mesa-dri libgl1-mesa-glx libegl-mesa0 \
    mesa-vulkan-drivers libvulkan1 libsdl2-2.0-0 libopenal1 \
    libfreetype6 liblua5.1-0 libicu72 curl procps \
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

COPY --chown=user:user --from=python-build /src/openra-rl/openra_env/ /home/user/app/openra_env/
COPY --chown=user:user --from=python-build /src/openra-rl/proto/ /home/user/app/proto/
COPY --chown=user:user --from=python-build /src/openra-rl/pyproject.toml /home/user/app/
COPY --chown=user:user hf_space_server.py /home/user/app/hf_space_server.py

COPY --chown=user:user space-entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && \
    chmod +x /entrypoint.sh

RUN install -d -o user -g user \
    "$XDG_CONFIG_HOME" \
    "$XDG_CONFIG_HOME/openra" \
    "$XDG_CONFIG_HOME/openra/Content" \
    "$XDG_CONFIG_HOME/openra/Content/ra" \
    "$XDG_CONFIG_HOME/openra/Content/ra/v2" \
    "$XDG_CONFIG_HOME/openra/Content/ra/v2/expand" \
    "$XDG_CONFIG_HOME/openra/Content/ra/v2/cnc" \
    "$XDG_CONFIG_HOME/openra/Logs" \
    "$XDG_CONFIG_HOME/openra/Replays" && \
    chown -R user:user "$XDG_CONFIG_HOME/openra" && \
    ( curl -sfL --max-time 30 -o /tmp/ra-quickinstall.zip \
        https://openra.baxxster.no/openra/ra-quickinstall.zip && \
    apt-get update && apt-get install -y --no-install-recommends unzip && \
    unzip -o /tmp/ra-quickinstall.zip -d /tmp/ra-content && \
    cp /tmp/ra-content/*.mix "$XDG_CONFIG_HOME/openra/Content/ra/v2/" && \
    cp /tmp/ra-content/expand/* "$XDG_CONFIG_HOME/openra/Content/ra/v2/expand/" && \
    cp /tmp/ra-content/cnc/* "$XDG_CONFIG_HOME/openra/Content/ra/v2/cnc/" && \
    chown -R user:user "$XDG_CONFIG_HOME/openra" && \
    rm -rf /tmp/ra-quickinstall.zip /tmp/ra-content && \
    apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    ) || echo "WARNING: RA content download failed (matches may fail to start)"

ENV OPENRA_PATH=/opt/openra
ENV OPENRA_MOUNT_PATH=/openra
ENV OPENRA_INTERNAL_BASE_URL=http://localhost:8000/openra
ENV PYTHONPATH=/home/user/app
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_ROLL_FORWARD=LatestMajor
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV MESA_GL_VERSION_OVERRIDE=3.3
ENV AI_SLOT=Multi0
ENV BOT_TYPE=easy
ENV RECORD_REPLAYS=true

EXPOSE 8000

USER user

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "hf_space_server"]
