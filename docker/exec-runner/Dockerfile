# syntax=docker/dockerfile:1
# Sandbox image for running student programs. See docs/execution-gate.md.
# Base: eclipse-temurin 25 JDK on Ubuntu noble, pinned by the multi-arch index digest.
# A new base is reviewed by hand (Dependabot docker updates for this directory are not auto-merged).
# Editing this file or driver.py requires a version bump here, in program_runner.py
# (RUNNER_VERSION, RUNNER_IMAGE and RUNNER_SOURCE_SHA256) and in docker-compose.yml.
FROM eclipse-temurin:25-jdk-noble@sha256:ba0f0e1a33864105e1d454e88105c617ab40f71062db1f306bf58be32a3f8e5e

LABEL org.cqc.exec-runner.version="2"

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ gcc libc6-dev python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin runner

# The only file copied in. Root-owned and not writable by the runner user.
COPY --chown=root:root driver.py /opt/exec-runner/driver.py
RUN chmod 0755 /opt/exec-runner && chmod 0444 /opt/exec-runner/driver.py

WORKDIR /work
USER 10001:10001
ENTRYPOINT ["python3", "-I", "/opt/exec-runner/driver.py"]
