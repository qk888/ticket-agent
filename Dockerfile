# ─────────────────────────────────────────────────────────────
# 售后工单处置 Agent —— 单容器镜像
#
# 注：原第 1 行是 `# syntax=docker/dockerfile:1`，已移除。
#     本文件只用 ARG / 多阶段 FROM / RUN / WORKDIR / COPY --from /
#     LABEL / USER / ENV / EXPOSE / HEALTHCHECK / ENTRYPOINT / CMD ——
#     全是 Dockerfile 前端内建语法，没有任何需要额外 frontend 的能力。
#     留着那行的唯一效果是逼 BuildKit 先去 docker.io 拉 14.28MB 的
#     frontend 镜像；本机到 docker.io 慢且频繁停摆，构建会永久挂在那里。
#
# 三条设计理由（都对应踩过的坑，不是抄模板）：
#   1. 多阶段构建：builder 装 ruff/mypy/pytest 那套 dev 依赖，
#      最终镜像只留运行时依赖。镜像从 ~1.1GB 降到 ~400MB。
#   2. 为什么用 python:3.12-slim 而不是 alpine：SQLAlchemy 的
#      C 扩展 + psycopg 在 alpine 上没有 wheel，要在容器里编译，
#      一次构建十几分钟。slim 有 manylinux wheel，pip 直接装。
#   3. 为什么不用 root：uvicorn 不该以 root 跑。这里建一个普通用户。
# ─────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.12-slim

# ── 阶段 1：装依赖 ────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS builder

# ★ 走国内 apt 镜像。base 是 Debian 13 (trixie)，出厂源是 deb.debian.org，
#   本机实测它在大文件上只有 69 KiB/s 且频繁停摆（Packages.xz 13 秒只下到 0.88 MiB）。
#   为什么是中科大而不是更快的腾讯云：腾讯云 trixie-updates 的 InRelease
#   落后 7 天（Date=10-09、Valid-Until=10-16 02:10，构建时已过期），
#   apt 会直接 exit 100 拒绝更新；中科大/清华/阿里云/官方都是当天新鲜。
#   实测中科大 5344 kB/s 且三套 suite 全新鲜 —— 快和不过期两头都占。
#   用 ARG 不写死：`--build-arg APT_MIRROR=deb.debian.org` 即切回官方源。
ARG APT_MIRROR=mirrors.ustc.edu.cn
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        [ -f "$f" ] || continue; \
        sed -i -e "s|http://deb.debian.org|https://${APT_MIRROR}|g" \
               -e "s|http://security.debian.org|https://${APT_MIRROR}|g" "$f"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# ★ 先把 pyproject 拷进去装依赖、再拷源码 —— 这样改代码不用重装依赖，
#   Docker 层缓存能命中。顺序反了的话每次改一行都要重跑一遍 pip install。
COPY pyproject.toml README.md ./
COPY src ./src

# ★ 走国内 PyPI 镜像。实测（docker 内直测 numpy wheel）：
#     腾讯云 9056 KiB/s、清华 1710 KiB/s、阿里云 428 KiB/s，
#     而 pypi.org 只有 12 KiB/s —— 索引页本身要约 201 秒，等于构建挂着不动。
#   用 ARG 不写死：`--build-arg PIP_INDEX_URL=https://pypi.org/simple` 切回官方源。
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
RUN pip install --no-cache-dir --retries 5 --timeout 30 --upgrade pip -i ${PIP_INDEX_URL} \
    && pip install --no-cache-dir --retries 5 --timeout 30 -i ${PIP_INDEX_URL} .

# ── 阶段 2：运行时 ────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

LABEL org.opencontainers.image.title="ticket-agent" \
      org.opencontainers.image.description="售后工单处置 Agent —— 工具调用 / HITL / 回归评测" \
      org.opencontainers.image.version="0.1.0"

# tini 当 1 号进程：容器收到停止信号时负责转发给 uvicorn 并回收僵尸进程。
# 少了它 `docker stop` 要等 10 秒才超时强杀。
# （apt 源已在本阶段前面改写过；每个 FROM 阶段是独立文件系统，所以这里要再来一次）
ARG APT_MIRROR=mirrors.ustc.edu.cn
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        [ -f "$f" ] || continue; \
        sed -i -e "s|http://deb.debian.org|https://${APT_MIRROR}|g" \
               -e "s|http://security.debian.org|https://${APT_MIRROR}|g" "$f"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini curl; \
    rm -rf /var/lib/apt/lists/*

# 非 root 运行
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/ticket-agent /usr/local/bin/ticket-agent
COPY src ./src
COPY web ./web
COPY prompt ./prompt
COPY pyproject.toml ./

# 数据与语料放卷里，不烧进镜像（语料 100+MB，烧进去每次改代码都重传）
RUN mkdir -p /app/eval_out /app/data /app/logs \
    && chown -R appuser:appuser /app
VOLUME ["/app/eval_out", "/app/data", "/app/logs"]

USER appuser

# ★ 让包从 /app/src 加载，而不是 site-packages 里 pip install 的那份。
#   原因：config.PROJECT_ROOT = Path(__file__).resolve().parents[2]。
#   装进 site-packages 后它解析成 /usr/local/lib/python3.12，
#   于是 PROJECT_ROOT/web、/data/raw、/eval_out、/prompt 全部错位 ——
#   `GET /` 恒返回 404「控制台文件缺失」就是它的症状。
#   PYTHONPATH 的优先级高于 site-packages，指回 /app/src 后
#   parents[2] 重新等于 /app，五条资源路径一次复位。
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    DATABASE_URL=sqlite:////app/eval_out/ticket_agent.db \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["ticket-agent", "serve", "--host", "0.0.0.0", "--port", "8000", "--seed", "60"]
