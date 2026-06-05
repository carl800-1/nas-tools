ARG ALPINE_MIRROR="mirrors.ustc.edu.cn"
ARG PYPI_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
ARG PYPI_TRUSTED_HOST="mirrors.aliyun.com"

FROM python:3.10.11-alpine
ARG ALPINE_MIRROR
ARG PYPI_INDEX_URL
ARG PYPI_TRUSTED_HOST

# 复制依赖文件（构建上下文是docker目录）
COPY package_list.txt /tmp/package_list.txt
COPY requirements.txt /tmp/requirements.txt

# 配置镜像源并安装依赖
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    echo "http://${ALPINE_MIRROR}/alpine/v3.18/main" > /etc/apk/repositories && \
    echo "http://${ALPINE_MIRROR}/alpine/v3.18/community" >> /etc/apk/repositories && \
    apk update && \
    apk add --no-cache --virtual .build-deps \
        libffi-dev \
        gcc \
        musl-dev \
        libxml2-dev \
        libxslt-dev \
        curl \
        bash \
        ca-certificates \
        tzdata \
        git \
        wget \
    && apk add --no-cache $(cat /tmp/package_list.txt) || true \
    && curl -sL https://github.com/just-containers/s6-overlay/releases/download/v2.2.0.3/s6-overlay-noarch.tar.xz | tarJxpf - / \
    && curl -sL https://github.com/just-containers/s6-overlay/releases/download/v2.2.0.3/s6-overlay-arch.tar.xz | tarJxpf - / \
    && curl https://rclone.org/install.sh | bash || echo "rclone install skipped" \
    && if [ "$(uname -m)" = "x86_64" ]; then ARCH=amd64; elif [ "$(uname -m)" = "aarch64" ]; then ARCH=arm64; fi \
    && curl https://dl.min.io/client/mc/release/linux-${ARCH}/mc --create-dirs -o /usr/bin/mc || echo "mc install skipped" \
    && chmod +x /usr/bin/mc 2>/dev/null || true \
    && pip config set global.index-url ${PYPI_INDEX_URL} && \
    pip config set global.trusted-host ${PYPI_TRUSTED_HOST} && \
    pip config set global.timeout 600 && \
    pip config set global.retries 10 && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir cython || true && \
    pip install --no-cache-dir -r /tmp/requirements.txt || true \
    && apk del --purge .build-deps 2>/dev/null || true \
    && rm -rf /tmp/* /root/.cache /var/cache/apk/*

ENV http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= \
    S6_SERVICES_GRACETIME=30000 \
    S6_KILL_GRACETIME=60000 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_SYNC_DISKS=1 \
    HOME="/nt" \
    TERM="xterm" \
    PATH=${PATH}:/usr/lib/chromium \
    LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    NASTOOL_CONFIG="/config/config.yaml" \
    NASTOOL_AUTO_UPDATE=false \
    NASTOOL_CN_UPDATE=true \
    NASTOOL_VERSION=main \
    PS1="\u@\h:\w \$ " \
    REPO_URL="https://github.com/carl800-1/nas-tools.git" \
    PYPI_MIRROR="${PYPI_INDEX_URL}" \
    ALPINE_MIRROR="${ALPINE_MIRROR}" \
    PUID=0 \
    PGID=0 \
    UMASK=000 \
    PYTHONWARNINGS="ignore:semaphore_tracker:UserWarning" \
    WORKDIR="/nas-tools"

WORKDIR ${WORKDIR}

RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    mkdir ${HOME} \
    && addgroup -S nt -g 911 \
    && adduser -S nt -G nt -h ${HOME} -s /bin/bash -u 911 \
    && python_ver=$(python3 -V | awk '{print $2}') \
    && python_path=$(which python3) \
    && [ -d "/usr/lib/python${python_ver%.*}/site-packages" ] || mkdir -p "/usr/lib/python${python_ver%.*}/site-packages" \
    && echo "${WORKDIR}/" > /usr/lib/python${python_ver%.*}/site-packages/nas-tools.pth \
    && echo 'fs.inotify.max_user_watches=5242880' >> /etc/sysctl.conf \
    && echo 'fs.inotify.max_user_instances=5242880' >> /etc/sysctl.conf \
    && echo "nt ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers \
    && git config --global pull.ff only \
    && git clone -b main ${REPO_URL} ${WORKDIR} --depth=1 --recurse-submodule \
    && git config --global --add safe.directory ${WORKDIR}

COPY --chmod=755 ./rootfs /
EXPOSE 3000
VOLUME ["/config"]
ENTRYPOINT [ "/init" ]