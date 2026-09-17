FROM node:22-alpine AS build
WORKDIR /app
RUN npm install --global pnpm@10.15.1
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY index.html vite.config.ts tsconfig.json .env .env.production ./
COPY src ./src
COPY public ./public
COPY build ./build
COPY types ./types
COPY scripts ./scripts
# These are public browser protocol values, never model keys or server secrets.
ARG VITE_LOGIN_AES_KEY=AxiomCampusKey16
ARG VITE_LOGIN_AES_IV=AxiomCampusIv16!
ARG VITE_REQUEST_SIGNATURE_SALT=AxiomSignSalt
ARG VITE_LOCAL_DEMO_USERNAME
ARG VITE_LOCAL_DEMO_PASSWORD
ENV VITE_LOGIN_AES_KEY=$VITE_LOGIN_AES_KEY \
    VITE_LOGIN_AES_IV=$VITE_LOGIN_AES_IV \
    VITE_REQUEST_SIGNATURE_SALT=$VITE_REQUEST_SIGNATURE_SALT \
    VITE_LOCAL_DEMO_USERNAME=$VITE_LOCAL_DEMO_USERNAME \
    VITE_LOCAL_DEMO_PASSWORD=$VITE_LOCAL_DEMO_PASSWORD
RUN pnpm build

FROM nginx:1.28-alpine
COPY deploy/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
