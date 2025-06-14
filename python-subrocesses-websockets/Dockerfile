FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \\
    gcc \\
    libc6-dev \\
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /workspace
RUN chown appuser:appuser /workspace

USER appuser

CMD ["/bin/bash"]
