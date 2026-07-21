# Frontend: build the Vite app, serve the static output with nginx.
# API calls are relative (/api/...) — see src/services/api.ts — so no
# build-time backend URL is needed; the reverse proxy (Caddy) routes /api
# to the backend container at request time.

FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
