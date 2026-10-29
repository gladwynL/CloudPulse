# syntax=docker/dockerfile:1

# ---- Build stage -----------------------------------------------------------
FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines VITE_* variables at build time (never put secrets here: they end up in the public
# bundle). The API URL must be reachable from the user's browser (not the Docker network), e.g.
# http://localhost:8000, or the special value `same-origin` to use the nginx proxy in this image.
ARG VITE_API_URL=http://localhost:8000
ARG VITE_APP_ENV=local
ENV VITE_API_URL=${VITE_API_URL} \
    VITE_APP_ENV=${VITE_APP_ENV}

RUN npm run build

# ---- Runtime stage ---------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy.conf /etc/nginx/cloudpulse-proxy.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1
