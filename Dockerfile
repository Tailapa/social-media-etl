FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY scripts/ scripts/
COPY migrations/ migrations/
COPY pyproject.toml .

# Gradio's launch() resolves server_name/server_port from these env vars
# (see app/gradio/app.py) -- 0.0.0.0 makes the app reachable from outside
# the container instead of only from inside it.
ENV GRADIO_SERVER_NAME=0.0.0.0
ENV GRADIO_SERVER_PORT=7860

EXPOSE 7860

CMD ["python", "scripts/launch_gradio.py"]
