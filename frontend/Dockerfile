# ── Frontend Dockerfile ───────────────────────────────────────────────────────
# Stage 1: Build the Vite/React app
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (cached layer)
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# ── Stage 2: Serve with Nginx ─────────────────────────────────────────────────
FROM nginx:1.27-alpine AS production

# Copy build output to nginx html root
COPY --from=builder /app/dist /usr/share/nginx/html

# Custom nginx config: proxy /api/* → backend:9000
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
