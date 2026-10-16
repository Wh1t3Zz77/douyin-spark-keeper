FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --include=optional
COPY . .
ARG NEXT_PUBLIC_ICP_NUMBER=""
ARG NEXT_PUBLIC_PUBLIC_SECURITY_NUMBER=""
ENV NEXT_PUBLIC_ICP_NUMBER=$NEXT_PUBLIC_ICP_NUMBER
ENV NEXT_PUBLIC_PUBLIC_SECURITY_NUMBER=$NEXT_PUBLIC_PUBLIC_SECURITY_NUMBER
RUN npm run build

FROM caddy:2.10-alpine
COPY --from=build /app/dist/client /srv
COPY deploy/Caddyfile.frontend /etc/caddy/Caddyfile
EXPOSE 3000
