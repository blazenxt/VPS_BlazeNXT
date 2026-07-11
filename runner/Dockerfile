FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini nodejs npm && rm -rf /var/lib/apt/lists/* && addgroup --system runner && adduser --system --ingroup runner --home /workspace runner
COPY runner/entrypoint.py /usr/local/bin/blaze-runner
RUN chmod 0555 /usr/local/bin/blaze-runner && chown -R runner:runner /workspace
USER runner
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini","--","python","/usr/local/bin/blaze-runner"]
