# RELIF backend (`relif serve`) in a container. One dependency: PyYAML (reads the mounted relif.yaml).
# The store lives on a bind-mounted host folder at /data, so `relif verify|export|backup` keep working from the
# host CLI. Build with your uid/gid (compose.yaml passes them) so files stay yours on the host.
FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELIF_HOME=/data

WORKDIR /src
COPY pyproject.toml README.md ./
COPY relif ./relif
RUN pip install --no-cache-dir . \
    && rm -rf /src \
    && groupadd --gid "$GID" relif \
    && useradd --uid "$UID" --gid "$GID" --no-create-home --shell /usr/sbin/nologin relif \
    && mkdir -p /data && chown "$UID:$GID" /data

WORKDIR /data
USER relif
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["relif"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
