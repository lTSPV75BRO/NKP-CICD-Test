FROM nginx:alpine
RUN apk add --no-cache curl jq
COPY flux-status.sh /usr/local/bin/
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["/bin/sh", "-c", "/usr/local/bin/flux-status.sh & nginx -g 'daemon off;'"]

