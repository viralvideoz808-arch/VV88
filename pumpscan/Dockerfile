FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY pumpscan ./pumpscan
RUN pip install --no-cache-dir .

# Hosted defaults: listen on all interfaces, start scanning at boot, keep signals in /data.
# PUMPSCAN_PASSWORD must be set or the server refuses to start.
ENV PUMPSCAN_HOST=0.0.0.0 PUMPSCAN_AUTOSTART=1 PUMPSCAN_DATA_DIR=/data PORT=8088
VOLUME ["/data"]
EXPOSE 8088
CMD ["pumpscan", "--no-browser"]
