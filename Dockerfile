FROM python:3.12-slim

# FFmpeg (with libass) does all editing; fonts give the captions their look.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-inter fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY vv88 ./vv88
RUN pip install --no-cache-dir ".[all]"

# Mount config.yaml, campaigns.yaml, .env and a data/ volume into /work.
WORKDIR /work
VOLUME ["/work/data"]
ENTRYPOINT ["vv88"]
CMD ["run", "--interval", "30"]
