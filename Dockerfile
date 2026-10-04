FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    ILARR_HOST=0.0.0.0 \
    ILARR_DB=/config/ilarr.db \
    ILARR_LIBRARY=/library

WORKDIR /app
COPY ilarr ./ilarr
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# No third-party dependencies, so nothing to pip install.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /config /library

# Starts as root only to fix /config ownership, then drops to PUID:PGID (default 1000:1000).
VOLUME ["/config", "/library"]
EXPOSE 8989
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8989/api/series', timeout=4)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "ilarr", "-c", "/config/config.json", "serve"]
