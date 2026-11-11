FROM alpine:3.22
RUN apk add --no-cache sqlite rclone tzdata bash coreutils
COPY backup.sh restore.sh loop.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh
CMD ["loop.sh"]
