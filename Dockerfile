FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8080
WORKDIR /opt/profiler
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 profiler \
    && useradd --uid 1000 --gid profiler --no-create-home --shell /usr/sbin/nologin profiler \
    && mkdir /data && chown profiler:profiler /data
COPY app ./app
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=100s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/api/health',timeout=4)"
CMD ["python", "-m", "app.main"]
