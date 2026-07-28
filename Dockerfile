# harness-tuner: rung one of three.
#
# This image exists so that a machine with no suitable Python, and no ability to
# install one, is not shut out. That is a real situation on locked-down build
# boxes and it is the reason "machine agnostic" needs more than a pip install.
#
# There is nothing to compile and nothing to fetch. The engine is standard
# library only, so this is a base image plus a copy.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="harness-tuner"
LABEL org.opencontainers.image.description="Measure an agent harness, then prove a change to it helped. Reference implementation of HTP-1."
LABEL org.opencontainers.image.source="https://github.com/csnyder256/harness-tuner"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /opt/harness-tuner

COPY harness_tuner/ ./harness_tuner/
COPY packs/ ./packs/
COPY conformance/ ./conformance/
COPY tools/ ./tools/
COPY AGENT-GUIDE.md LICENSE ./

ENV PYTHONPATH=/opt/harness-tuner
ENV PYTHONDONTWRITEBYTECODE=1

# Prove the image is sound at build time rather than discovering it on someone
# else's machine. If the conformance suite does not pass, there is no image.
RUN python -m harness_tuner conformance

# Runs are written where the user mounted their work, not inside the image.
WORKDIR /work

ENTRYPOINT ["python", "-m", "harness_tuner"]
CMD ["--help"]
