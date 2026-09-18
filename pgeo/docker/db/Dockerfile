# pgeo-db: PostgreSQL 18 + PostGIS 3.6 + libpostal (pgsql-postal extension) + pg_search.
#
# - Base: official postgis/postgis image, pinned by index digest.
# - libpostal library and pgsql-postal compiled from pinned upstream commits.
# - libpostal model data (v1.0.0, ~1.9 GB) copied from the Pelias libpostal-service image
#   already used by the Pelias stack, instead of downloading it again.
# - ParadeDB pg_search .deb pinned by version and SHA-256 (A/B arm; CREATE EXTENSION only
#   when that arm is enabled).
ARG POSTGIS_IMAGE=postgis/postgis:18-3.6@sha256:60f6ad1d21ea86a67d47780b9a0d1e1d200500f62b19293fa834d0dea80b8677
ARG MODEL_IMAGE=pelias/libpostal-service:master-2026-07-08-61c5d6e058f021c42b33781f0b5fd566fdec954d

FROM ${MODEL_IMAGE} AS model

FROM ${POSTGIS_IMAGE} AS build
# libpostal v1.1 and pgsql-postal master at review time
ARG LIBPOSTAL_REF=9c975972985b54491e756efd70e416f18ff97958
ARG PGSQL_POSTAL_REF=105a6ca855dbf4d33b160ca50d00c3c76a7e2061
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential autoconf automake libtool pkg-config git ca-certificates \
      postgresql-server-dev-18 \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/openvenues/libpostal /src/libpostal \
 && cd /src/libpostal && git checkout "${LIBPOSTAL_REF}" \
 && ./bootstrap.sh \
 && ./configure --datadir=/usr/share/libpostal --disable-data-download \
 && make -j"$(nproc)" && make install && ldconfig
RUN git clone https://github.com/pramsey/pgsql-postal /src/pgsql-postal \
 && cd /src/pgsql-postal && git checkout "${PGSQL_POSTAL_REF}" \
 && make POSTAL_INCLUDE=/usr/local/include POSTAL_LIBS=/usr/local/lib \
 && make install

FROM ${POSTGIS_IMAGE}
ARG PG_SEARCH_VERSION=0.25.9
ARG PG_SEARCH_SHA256=8fd29b73f39da221bc9d2414d3a5f7245e588509a583283e33ce5c1f2d777583
# All libpostal shared-object names (the soname version depends on the release).
COPY --from=build /usr/local/lib/libpostal.so* /usr/local/lib/
RUN ldconfig
COPY --from=build /usr/lib/postgresql/18/lib/postal.so /usr/lib/postgresql/18/lib/
COPY --from=build /usr/share/postgresql/18/extension/postal.control \
                  /usr/share/postgresql/18/extension/postal--1.0.sql \
                  /usr/share/postgresql/18/extension/
COPY --from=model /usr/share/libpostal /usr/share/libpostal
ADD https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/postgresql-18-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-trixie_amd64.deb /tmp/pg_search.deb
RUN echo "${PG_SEARCH_SHA256}  /tmp/pg_search.deb" | sha256sum -c - \
 && apt-get update && apt-get install -y --no-install-recommends /tmp/pg_search.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/pg_search.deb
