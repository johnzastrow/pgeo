# pgeo-build: the pre-processor, as one image. Fetches the open data for a region, builds the
# geocoder database, dumps it, and pushes the dump to a host running the serving bundle.
#
# It carries its own PostgreSQL so a workstation needs nothing but Docker. The base is the same
# postgis/postgis:18-3.6 the serving bundle uses, which is what makes the dump restorable there:
# pg_restore accepts the same major or older, and sharing the base settles that by construction.
#
# Only core and contrib extensions are needed to build (postgis, pg_trgm, unaccent,
# fuzzystrmatch). libpostal and pg_search are optional arms of the *query* side and live in
# pgeo-db, so this image does not carry libpostal's 1.9 GB of model data.
ARG POSTGIS_IMAGE=postgis/postgis:18-3.6@sha256:60f6ad1d21ea86a67d47780b9a0d1e1d200500f62b19293fa834d0dea80b8677

FROM ghcr.io/astral-sh/uv:0.9.17 AS uv

FROM ${POSTGIS_IMAGE}
COPY --from=uv /uv /usr/local/bin/uv

# gdal-bin is ogr2ogr, which loads the OpenStreetMap extracts. The rest is the fetch path:
# curl and ca-certificates for the publishers, jq for the region registry, bzip2/unzip for what
# they ship, rsync and openssh-client for the push.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gdal-bin jq curl ca-certificates bzip2 unzip rsync openssh-client procps \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /opt/pgeo

# Both Python projects, each in its own environment: pgeo loads and serves, pelias-prep turns
# GNIS, ZCTA and Overture into the CSV the loader reads. Dependencies first, so a change to the
# source does not re-resolve them.
COPY pgeo/pyproject.toml pgeo/uv.lock /opt/pgeo/pgeo/
COPY prep/pyproject.toml prep/uv.lock /opt/pgeo/prep/
RUN uv sync --frozen --no-dev --no-install-project --project /opt/pgeo/pgeo \
 && uv sync --frozen --no-dev --no-install-project --project /opt/pgeo/prep
COPY pgeo/src /opt/pgeo/pgeo/src
COPY prep/src /opt/pgeo/prep/src
RUN uv sync --frozen --no-dev --project /opt/pgeo/pgeo \
 && uv sync --frozen --no-dev --project /opt/pgeo/prep

# The SQL the build applies, the region registry, and the scripts the entrypoint drives.
COPY pgeo/sql /opt/pgeo/pgeo/sql
COPY pgeo/tuning /opt/pgeo/pgeo/tuning
COPY regions /opt/pgeo/regions
COPY scripts /opt/pgeo/scripts
COPY tests/accuracy /opt/pgeo/tests/accuracy
COPY pgeo/docker/build/entrypoint.sh /usr/local/bin/pgeo-build
RUN chmod +x /usr/local/bin/pgeo-build

# Everything the build reads or writes lives under /data, which is meant to be a volume: the
# sources cache there, so a second region or a second build costs only what changed.
ENV PGEO_DATA_DIR=/data \
    PGDATA=/data/pg \
    PGEO_BUILD=me \
    PATH=/opt/pgeo/pgeo/.venv/bin:/opt/pgeo/prep/.venv/bin:$PATH
# Both Python projects work out their data directory from the repository root, so /data is the
# same place by either route; without this, prep looks in /opt/pgeo/data and finds nothing.
RUN ln -s /data /opt/pgeo/data
VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/pgeo-build"]
CMD ["help"]
