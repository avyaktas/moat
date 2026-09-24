FROM python:3.12-slim

# PYTHONUNBUFFERED, not PYTHONBUFFERED. The original spelling is not a
# variable Python reads, so it did nothing: stdout stayed block-buffered, and
# log lines sat in a 4KB buffer instead of reaching the platform's log
# collector. On a service that logs a few hundred bytes per request that can
# mean logs appearing minutes late, or never, if the process is killed with
# the buffer unflushed - which is exactly when the logs are wanted.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Dependencies first, in their own layer: application code changes on every
# commit while requirements rarely do, so this layer stays cached across
# almost every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. The default is root, which means a flaw in any
# dependency runs as root inside the container.
RUN useradd --create-home --uid 1000 moat && chown -R moat:moat /code
USER moat

EXPOSE 8000

# Meaningful now that /health actually queries the database - it used to
# return ok unconditionally, so a healthcheck against it would have reported
# a container healthy while every request failed on a dead pool.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
