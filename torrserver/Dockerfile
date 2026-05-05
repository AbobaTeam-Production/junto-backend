FROM alpine:3.20

# gcompat provides a glibc-compatible loader so the upstream TorrServer binary
# (linked against glibc) runs on this musl-based Alpine image.
RUN apk add --no-cache wget gcompat libstdc++ ca-certificates

RUN mkdir -p /opt/torrserver \
 && wget -q -O /opt/torrserver/torrserver \
        https://github.com/YouROK/TorrServer/releases/latest/download/TorrServer-linux-amd64 \
 && chmod +x /opt/torrserver/torrserver

EXPOSE 8090

ENTRYPOINT ["/opt/torrserver/torrserver", \
            "--port", "8090", \
            "--path", "/opt/torrserver/config", \
            "--torrentsdir", "/opt/torrserver/torrents"]
