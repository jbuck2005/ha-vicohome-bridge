# The base image is pinned here on purpose: some Supervisor versions ignore build.yaml and
# pass their own BUILD_FROM (an Alpine base without pip).
FROM python:3.12-alpine3.20

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apk add --no-cache ffmpeg
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/

CMD ["python3", "-u", "/app/bridge.py"]
