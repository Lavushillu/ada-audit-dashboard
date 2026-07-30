FROM docker.ci.artifacts.walmart.com/wce-docker/alpine:3-main

LABEL maintainer="l0p0c77@walmart.com"

# Install Python 3 and pip as root
USER root
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# Install dependencies (exclude browser_cookie3 — not needed on server)
COPY --chown=10000:10000 requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages \
    $(grep -v 'browser-cookie3' requirements.txt | tr '\n' ' ')

# Copy application source
COPY --chown=10000:10000 main.py jira_client.py settings.json ./
COPY --chown=10000:10000 templates/ ./templates/

# Run as non-root (GBI provides UID 10000)
USER 10000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
