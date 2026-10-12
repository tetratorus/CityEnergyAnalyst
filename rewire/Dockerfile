# Rewire image: the repository's own CEA image (../Dockerfile, unmodified) plus the thin rewire/ glue.
#   docker build -t cea-upstream:local .                         (from the repo root)
#   docker build -t cea-rewire:local -f rewire/Dockerfile rewire  (from the repo root)
ARG CEA_IMAGE=cea-upstream:local
FROM ${CEA_IMAGE}
# root so the container can read/write the spine runner's private temp dirs (mode 0700, bind-mounted)
USER root
COPY . /rewire
WORKDIR /rewire
ENTRYPOINT ["/usr/local/bin/_entrypoint.sh"]
CMD ["python", "-c", "import cea; print(cea.__version__)"]
