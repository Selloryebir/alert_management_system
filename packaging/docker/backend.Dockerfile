FROM node:22.22.1-bookworm-slim@sha256:4f77a690f2f8946ab16fe1e791a3ac0667ae1c3575c3e4d0d4589e9ed5bfaf3d AS frontend-build

WORKDIR /workspace/src/frontend
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci
COPY src/frontend/index.html src/frontend/tsconfig.json src/frontend/vite.config.ts ./
COPY src/frontend/src ./src
RUN npm run build

FROM eclipse-temurin:21-jdk-noble@sha256:75ce56643243c3db632be2ef259625fb42ee3be1334389659f7a1a61acb78783 AS backend-build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY .mvn .mvn
COPY mvnw ./mvnw
COPY src/backend/pom.xml src/backend/pom.xml
RUN ./mvnw -f src/backend/pom.xml dependency:go-offline
COPY src/backend/src src/backend/src
COPY --from=frontend-build /workspace/src/frontend/dist src/frontend/dist
RUN ./mvnw -f src/backend/pom.xml package -DskipTests

FROM eclipse-temurin:21-jre-noble@sha256:96975602e131485862eb8cd32927face8a06d7591a5e865944b634a701d9df72

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 alertdemo \
    && useradd --system --uid 10001 --gid alertdemo --home-dir /app alertdemo
WORKDIR /app
COPY --from=backend-build /workspace/src/backend/target/alert-management-backend-0.1.0.jar /app/core-api.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/core-api.jar"]
