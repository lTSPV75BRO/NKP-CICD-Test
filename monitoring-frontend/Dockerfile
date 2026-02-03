FROM nginx:alpine

COPY index.html /usr/share/nginx/html/index.html
# SPA: serve index.html for all paths so refresh works when using Ingress
RUN echo 'server { root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ /index.html; } }' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
