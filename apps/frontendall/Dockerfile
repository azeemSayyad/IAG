FROM nginx:1.27-alpine

COPY . /usr/share/nginx/html

# nginx.conf is a TEMPLATE, not a finished config: the nginx image's entrypoint
# runs envsubst over /etc/nginx/templates/*.template into /etc/nginx/conf.d/ at
# container start. That's what lets the SAME image serve Docker Compose (where
# the backend is the compose service `backend-api:8000`) and Railway (where it
# is `<service>.railway.internal:8000`) — the target is an env var, not baked in.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Writes the DNS resolver nginx needs to re-resolve the backend per request.
COPY docker-entrypoint.d/10-resolver.sh /docker-entrypoint.d/10-resolver.sh
RUN chmod +x /docker-entrypoint.d/10-resolver.sh

# Only BACKEND_HOST and PORT are substituted. Without this filter envsubst would
# also eat nginx's own runtime variables ($host, $remote_addr, $http_upgrade …)
# and blank them out, silently breaking proxying and WebSocket upgrades.
ENV NGINX_ENVSUBST_FILTER="^(BACKEND_HOST|PORT)$"

# Defaults keep docker-compose working with no extra config. Railway overrides
# both: PORT is injected by the platform, BACKEND_HOST is set to the backend's
# private domain (see docs/RAILWAY_DEPLOY.md).
ENV BACKEND_HOST=backend-api:8000
ENV PORT=80

EXPOSE 80
