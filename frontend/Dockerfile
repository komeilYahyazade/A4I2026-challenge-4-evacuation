# Build the SPA, then run a zero-dependency Node server that serves dist/ and proxies the agent.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production PORT=8080
COPY --from=build /app/dist ./dist
COPY server.mjs ./
EXPOSE 8080
CMD ["node", "server.mjs"]
