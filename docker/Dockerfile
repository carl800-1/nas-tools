
FROM python:3.10.11-slim-bullseye
COPY docker/requirements.txt /tmp/requirements.txt
COPY docker/package_list_debian.txt /tmp/package_list_debian.txt
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe && \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get update -y && \
    apt-get install -y wget bash ca-certificates locales curl unzip git && \
    locale-gen zh_CN.UTF-8 && \
    apt-get install -y $(cat /tmp/package_list_debian.txt) || true
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    mkdir -p /usr/lib/chromium && \
    ln -sf /usr/bin/chromedriver /usr/lib/chromium/chromedriver 2>/dev/null || true && \
    update-alternatives --install /usr/bin/python python /usr/local/bin/python3.10 3 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/local/bin/python3.10 3
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe && \
    curl https://rclone.org/install.sh | bash || echo "rclone install skipped" && \
    if [ "$(uname -m)" = "x86_64" ]; then ARCH=amd64; elif [ "$(uname -m)" = "aarch64" ]; then ARCH=arm64; fi && \
    curl https://dl.min.io/client/mc/release/linux-${ARCH}/mc --create-dirs -o /usr/bin/mc || echo "mc install skipped" && \
    chmod +x /usr/bin/mc 2>/dev/null || true && \
    apt-get install -y build-essential || true && \
    pip config set global.timeout 600 && \
    pip config set global.retries 10 && \
    pip config set global.fetch-size 1048576 && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir cython || true
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe && \
    pip install --no-cache-dir --compile -r /tmp/requirements.txt || true
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe && \
    apt-get remove -y build-essential 2>/dev/null || true && \
    apt-get autoremove -y 2>/dev/null || true && \
    apt-get clean -y && \
    rm -rf \
        /tmp/* \
        /root/.cache \
        /var/lib/apt/lists/* \
        /var/tmp/*
ENV http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= \
    HOME="/nt" \
    TERM="xterm" \
    PATH=${PATH}:/usr/lib/chromium \
    TZ="Asia/Shanghai" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    NASTOOL_CONFIG="/config/config.yaml" \
    NASTOOL_AUTO_UPDATE=false \
    NASTOOL_CN_UPDATE=true \
    NASTOOL_VERSION=main \
    REPO_URL="https://github.com/carl800-1/nas-tools.git" \
    PUID=0 \
    PGID=0 \
    UMASK=000 \
    PYTHONWARNINGS="ignore:semaphore_tracker:UserWarning" \
    WORKDIR="/nas-tools"
WORKDIR ${WORKDIR}
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    set -xe \
    && mkdir -p ${HOME} \
    && groupadd -r nt -g 911 \
    && useradd -r nt -g nt -d ${HOME} -s /bin/bash -u 911 \
    && python_ver=$(python3 -V | awk '{print $2}') \
    && echo "${WORKDIR}/" > /usr/local/lib/python${python_ver%.*}/site-packages/nas-tools.pth \
    && echo 'fs.inotify.max_user_watches=5242880' >> /etc/sysctl.conf \
    && echo 'fs.inotify.max_user_instances=5242880' >> /etc/sysctl.conf \
    && echo "nt ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
COPY --chmod=755 docker/rootfs /
COPY --chmod=755 . /nas-tools/
RUN chmod +x /nas-tools/run.py 2>/dev/null || true
EXPOSE 3000
VOLUME [ "/config" ]
CMD [ "python", "/nas-tools/run.py" ]