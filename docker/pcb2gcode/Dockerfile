# pcb2gcode built from a pinned upstream tag.
#
#   docker build -t pcbcam/pcb2gcode:3.0.4 docker/pcb2gcode
#   docker run --rm -v "$PWD:/work" -w /work pcbcam/pcb2gcode:3.0.4 --version

ARG UBUNTU=ubuntu:24.04

FROM ${UBUNTU} AS build
ARG PCB2GCODE_REF=v3.0.4
# boost::geometry translation units need ~1.5 GB RAM each; keep this modest.
ARG JOBS=4
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config git ca-certificates \
        libboost-program-options-dev libgtkmm-2.4-dev gerbv \
        librsvg2-dev libgeos++-dev libgeos-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*
# Ubuntu 24.04 ships libgerbv.pc with an empty Version field (see upstream setup-ubuntu.sh).
RUN pc=$(find /usr/lib -name libgerbv.pc | head -n1) \
    && sed -i 's/^Version:[[:space:]]*$/Version: 2.10.0/' "$pc"
RUN git clone --depth 1 --branch "${PCB2GCODE_REF}" https://github.com/pcb2gcode/pcb2gcode.git /src
WORKDIR /src/build
RUN cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
        -DPCB2GCODE_COMPILE_WARNING_AS_ERROR=OFF -DPCB2GCODE_USE_CCACHE=OFF \
    && make -j"${JOBS}" \
    && strip pcb2gcode \
    && mkdir -p /out/lib \
    && cp pcb2gcode /out/ \
    && ldd pcb2gcode | awk '/=> \// {print $3}' \
        | grep -vE '/(libc|libm|libpthread|libdl|librt|ld-linux)[.-]' \
        | xargs -I{} cp -L {} /out/lib/

FROM ${UBUNTU}
COPY --from=build /out/pcb2gcode /usr/local/bin/pcb2gcode
COPY --from=build /out/lib/ /usr/local/lib/pcb2gcode/
ENV LD_LIBRARY_PATH=/usr/local/lib/pcb2gcode
WORKDIR /work
ENTRYPOINT ["pcb2gcode"]
