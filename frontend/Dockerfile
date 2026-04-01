# ── Frontend Dockerfile ─────────────────────────────────────
FROM node:20-alpine

WORKDIR /app

# Install dependencies first for caching
COPY package.json package-lock.json ./
RUN npm ci && npm install -g serve

# Build the React app
COPY . .
ARG REACT_APP_API_URL=http://localhost:8000
ENV REACT_APP_API_URL=$REACT_APP_API_URL
RUN npm run build

EXPOSE 3000

CMD ["serve", "-s", "build", "-l", "3000"]
