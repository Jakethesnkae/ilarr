FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    ILARR_HOST=0.0.0.0 \
    ILARR_DB=/config/ilarr.db \
    ILARR_LIBRARY=/library

WORKDIR /app
COPY ilarr ./ilarr

# No third-party dependencies, so nothing to pip install.
RUN useradd -u 1000 -m ilarr && mkdir -p /config /library && chown ilarr /config /library
USER ilarr

VOLUME ["/config", "/library"]
EXPOSE 8989
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8989/api/series', timeout=4)"

CMD ["python", "-m", "ilarr", "-c", "/config/config.json", "serve"]
