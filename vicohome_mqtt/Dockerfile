# A glibc base (not Alpine) so aiortc and its native deps (PyAV, pylibsrtp, cryptography) install
# from prebuilt wheels on amd64 and aarch64 instead of compiling on the Home Assistant host.
# Pinned on purpose: some Supervisor versions ignore build.yaml and pass their own BUILD_FROM.
FROM python:3.15-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/

CMD ["python3", "-u", "/app/bridge.py"]
