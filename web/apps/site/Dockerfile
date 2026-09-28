# Build context: web/  (docker build -f apps/site/Dockerfile web)
FROM node:26-alpine AS build
WORKDIR /web
RUN npm install -g pnpm@11.2.2
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages ./packages
COPY apps/site ./apps/site
RUN pnpm install --frozen-lockfile --filter @litetraffic/site... \
 && pnpm -C apps/site build

FROM nginx:1.29-alpine
COPY apps/site/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/apps/site/dist /usr/share/nginx/html
EXPOSE 80
