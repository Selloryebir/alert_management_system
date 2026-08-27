FROM python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0
RUN groupadd --system --gid 10001 alertdemo \
    && useradd --system --uid 10001 --gid alertdemo --home-dir /app alertdemo
WORKDIR /app
COPY src/algorithm/requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --no-deps --requirement requirements.lock
COPY src/algorithm/algorithm_service ./algorithm_service
COPY tools/model-training ./model-training
USER 10001:10001
EXPOSE 8001
ENTRYPOINT ["python", "-m", "algorithm_service"]
