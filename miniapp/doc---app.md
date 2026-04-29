# Исходный код проекта (./app)

Это полный исходный код проекта, внимательно изучите структуру проекта и содержимое файлов.

## Структура проекта
```
├── .github
│   └── .github/workflows
│       ├── .github/workflows/deploy-api.yml
│       └── .github/workflows/deploy-web.yml
├── api
│   ├── api/Dockerfile
│   ├── api/Dockerfile.local
│   ├── api/entrypoint.sh
│   ├── api/nginx-api.conf
│   ├── api/package.json
│   ├── api/prisma
│   │   ├── api/prisma/migrations
│   │   │   ├── api/prisma/migrations/20250603190457_pervaya_migracziya_posle_dobavleniya
│   │   │   │   └── api/prisma/migrations/20250603190457_pervaya_migracziya_posle_dobavleniya/migration.sql
│   │   │   └── api/prisma/migrations/migration_lock.toml
│   │   └── api/prisma/schema.prisma
│   ├── api/src
│   │   └── api/src/index.ts
│   ├── api/start-dev.sh
│   └── api/tsconfig.json
├── docker-compose-dev.yml
├── start-dev.sh
└── web
    ├── web/Dockerfile
    ├── web/Dockerfile.local
    ├── web/eslint.config.js
    ├── web/index.html
    ├── web/init-letsencrypt.sh
    ├── web/nginx.conf
    ├── web/package.json
    ├── web/public
    ├── web/src
    │   ├── web/src/App.tsx
    │   ├── web/src/assets
    │   ├── web/src/main.tsx
    │   ├── web/src/manifest.json
    │   └── web/src/vite-env.d.ts
    ├── web/start.sh
    ├── web/tsconfig.app.json
    ├── web/tsconfig.json
    ├── web/tsconfig.node.json
    └── web/vite.config.ts
```
## Содержимое файлов


## app/.github/workflows/deploy-api.yml
```
# .github/workflows/deploy-api.yml

name: Deploy API to Yandex Cloud 4

on:
  push:
    branches: [main]
    paths:
      - 'api/**'
      - '.github/workflows/deploy-api.yml'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Configure YC CLI
        run: |
          curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh -o install.sh
          chmod +x install.sh
          ./install.sh -i $HOME/yandex-cloud -n
          echo "$HOME/yandex-cloud/bin" >> $GITHUB_PATH
          $HOME/yandex-cloud/bin/yc --version

      - name: Get IAM Token
        run: |
          ~/yandex-cloud/bin/yc config set token ${{ secrets.OUATH_TOKEN }}
          ~/yandex-cloud/bin/yc config set cloud-id ${{ secrets.YC_CLOUD_ID }}
          ~/yandex-cloud/bin/yc config set folder-id ${{ secrets.YC_FOLDER_ID }}
          YC_TOKEN=$(~/yandex-cloud/bin/yc iam create-token)
          echo "YC_IAM_TOKEN=$YC_TOKEN" >> $GITHUB_ENV

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '20'

      - name: Install dependencies
        working-directory: api
        run: npm ci

      - name: Build and Push Docker Image
        working-directory: api
        run: |
          docker build \
            -t cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/mandala-api:${{ github.sha }} \
            -t cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/mandala-api:latest \
            .
          echo "${{ env.YC_IAM_TOKEN }}" | docker login -u iam --password-stdin cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}
          docker push cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/mandala-api:${{ github.sha }}
          docker push cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/mandala-api:latest

      - name: Update VM Container
        run: |
          {
            echo "DB_URL=${{ secrets.YC_DB_URL }}"
            echo "PORT=3000"
            echo "NODE_ENV=production"
          } > envfile

          ~/yandex-cloud/bin/yc compute instance update-container ${{ secrets.YC_VM_API_ID }} \
            --container-image cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/mandala-api:${{ github.sha }} \
            --container-restart-policy always \
            --container-env-file envfile

```


## app/.github/workflows/deploy-web.yml
```
name: Deploy to Yandex Cloud 2

on:
  push:
    branches: [main]
    paths:
      - 'web/**'
      - '.github/workflows/deploy-web.yml'
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Configure YC CLI
        run: |
          curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh -o install.sh
          chmod +x install.sh
          ./install.sh -i $HOME/yandex-cloud -n                
          echo "$HOME/yandex-cloud/bin" >> $GITHUB_PATH
          $HOME/yandex-cloud/bin/yc --version

      - name: Get IAM Token
        run: |
          ~/yandex-cloud/bin/yc config set token ${{ secrets.OUATH_TOKEN }}
          ~/yandex-cloud/bin/yc config set cloud-id ${{ secrets.YC_CLOUD_ID }}
          ~/yandex-cloud/bin/yc config set folder-id ${{ secrets.YC_FOLDER_ID }}         
          YC_TOKEN=$(~/yandex-cloud/bin/yc iam create-token)
          echo "YC_IAM_TOKEN=$YC_TOKEN" >> $GITHUB_ENV

      - name: Build and Push Docker Image
        working-directory: web
        run: |
          docker build \
          --build-arg VITE_API_URL=https://api.mandala-app.online \
          -t cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/miniapp-prod:${{ github.sha }} \
          -t cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/miniapp-prod:latest \
          .
          echo "${{ env.YC_IAM_TOKEN }}" | docker login -u iam --password-stdin cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}
          docker push cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/miniapp-prod:${{ github.sha }}
          docker push cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/miniapp-prod:latest

      - name: Update VM Container
        run: |
          ~/yandex-cloud/bin/yc compute instance update-container ${{ secrets.YC_VM_ID }} \
          --container-image cr.yandex/${{ secrets.YC_CONTAINER_REGISTRY_ID }}/miniapp-prod:${{ github.sha }} \
          --container-restart-policy always   

```


## app/api/Dockerfile
```
# Stage 1: Build
FROM node:20.12.2-alpine AS api-builder

RUN apk add --no-cache python3 make g++ git openssh-client

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-optional

COPY . .
COPY prisma ./prisma/
RUN npm run build

# Stage 2: Production (на node:20-alpine, а не nginx:alpine!)
FROM node:20.12.2-alpine

# Устанавливаем Nginx, postgresql-client и всё что тебе надо
RUN apk add --no-cache nginx postgresql-client bash openssl

WORKDIR /app

# Копируем билд и модули из билдера
COPY --from=api-builder /app/package.json /app/package-lock.json ./
COPY --from=api-builder /app/node_modules ./node_modules
COPY --from=api-builder /app/dist ./dist
COPY --from=api-builder /app/prisma ./prisma

RUN npm ci --only=production --no-optional

# Nginx config и энтрипойнт
COPY ./nginx-api.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /docker-entrypoint.d/entrypoint.sh
RUN chmod +x /docker-entrypoint.d/entrypoint.sh

EXPOSE 80 443

ENTRYPOINT ["/docker-entrypoint.d/entrypoint.sh"]

```


## app/api/Dockerfile.local
```
FROM node:20.12.2-alpine

WORKDIR /app

# Установка зависимостей
COPY package.json package-lock.json* ./
RUN npm install --silent --no-optional --no-fund

# Копируем остальные файлы
COPY . .
COPY prisma ./prisma/

# Генерируем клиент Prisma
RUN npx prisma generate

EXPOSE 3000

CMD ["npm", "run", "dev"]

```


## app/api/entrypoint.sh
```
#!/bin/bash
set -e

# Функция для инициализации схемы БД
init_db() {
    local max_retries=5
    local retry=0

    while [ $retry -lt $max_retries ]; do
        # Проверяем доступ к схеме через простой запрос
        if psql "$DB_URL" -c "SELECT 1 FROM pg_namespace WHERE nspname = 'mandala_app'" | grep -q 1; then
            echo "✅ Database schema exists"
            return 0
        else
            echo "⚠️ Schema 'mandala_app' not found, attempting to create..."
            if psql "$DB_URL" -c "CREATE SCHEMA IF NOT EXISTS mandala_app;
                GRANT USAGE ON SCHEMA mandala_app TO mandala_user;
                GRANT CREATE ON SCHEMA mandala_app TO mandala_user;" &>/dev/null; then
                echo "✅ Database schema initialized"
                return 0
            fi
        fi

        echo "⚠️ Failed to initialize database (attempt $((retry+1))/$max_retries)"
        sleep 5
        ((retry++))
    done
    return 1
}

# Функция для генерации Prisma Client
generate_prisma_client() {
    echo "Generating Prisma Client..."
    cd /app
    npx prisma generate
}

# Функция для применения миграций
apply_migrations() {
    echo "Applying database migrations..."
    cd /app
    npx prisma migrate deploy
}

# Основной процесс
if ! init_db || ! generate_prisma_client || ! apply_migrations; then
    echo "🛑 Database initialization failed, exiting..."
    exit 1
fi

# Запуск приложения
echo "🚀 Starting Node.js API"
node dist/index.js &

# Настройка Certbot
CERT_DIR="/etc/letsencrypt/live/api.mandala-app.online"
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "⚠️ Initializing Certbot..."
    certbot certonly --non-interactive --agree-tos --webroot \
        --webroot-path /var/www/certbot \
        -d api.mandala-app.online \
        -d api.mandala-app.ru \
        --email sshishkintolik@mail.ru \
        --staging || echo "⚠️ Certbot initialization failed"
fi

# Настройка cron для обновления сертификатов
echo "0 3 * * * /usr/bin/certbot renew --quiet --webroot -w /var/www/certbot" > /etc/crontabs/root
crond -l 2

echo "🚀 Starting Nginx"
exec nginx -g "daemon off;"

```


## app/api/nginx-api.conf
```
server {
    listen 80;
    server_name api.mandala-app.online api.mandala-app.ru;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl http2;
    server_name api.mandala-app.online api.mandala-app.ru;

    ssl_certificate /etc/letsencrypt/live/api.mandala-app.online/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.mandala-app.online/privkey.pem;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

```


## app/api/package.json
```
{
  "name": "api",
  "version": "1.0.0",
  "description": "",
  "main": "index.js",
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1",
    "dev": "ts-node src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "prisma:generate": "prisma generate",
    "prisma:migrate": "prisma migrate dev",
    "prisma:studio": "prisma studio",
    "prisma:push": "prisma db push",
    "prisma:deploy": "prisma migrate deploy"
  },
  "repository": {
    "type": "git",
    "url": "git+https://github.com/pandanax/mandala-api.git"
  },
  "keywords": [],
  "author": "",
  "license": "ISC",
  "bugs": {
    "url": "https://github.com/pandanax/mandala-api/issues"
  },
  "homepage": "https://github.com/pandanax/mandala-api#readme",
  "dependencies": {
    "@prisma/client": "^5.0.0",
    "express": "^4.18.2",
    "prisma": "^6.9.0"
  },
  "devDependencies": {
    "@types/express": "^5.0.2",
    "ts-node": "^10.9.1",
    "typescript": "^5.0.4"
  }
}

```


## app/api/prisma/migrations/20250603190457_pervaya_migracziya_posle_dobavleniya/migration.sql
```
-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "mandala_app";

-- CreateTable
CREATE TABLE "mandala_app"."User" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "mandala_app"."UserData" (
    "userId" TEXT NOT NULL,
    "profileUrl" TEXT,
    "metadata" JSONB,

    CONSTRAINT "UserData_pkey" PRIMARY KEY ("userId")
);

-- CreateIndex
CREATE UNIQUE INDEX "User_email_key" ON "mandala_app"."User"("email");

-- AddForeignKey
ALTER TABLE "mandala_app"."UserData" ADD CONSTRAINT "UserData_userId_fkey" FOREIGN KEY ("userId") REFERENCES "mandala_app"."User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

```


## app/api/prisma/migrations/migration_lock.toml
```
# Please do not edit this file manually
# It should be added in your version-control system (e.g., Git)
provider = "postgresql"

```


## app/api/prisma/schema.prisma
```
generator client {
  provider = "prisma-client-js"
  previewFeatures = ["multiSchema"]
}

datasource db {
  provider = "postgresql"
  url      = env("DB_URL")
  schemas  = ["mandala_app"]
}

model User {
  @@schema("mandala_app")  // Добавь эту строку
  id        String   @id @default(uuid())
  email     String   @unique
  createdAt DateTime @default(now())
  data      UserData?
}

model UserData {
  @@schema("mandala_app")  // И здесь
  userId     String  @id
  user       User    @relation(fields: [userId], references: [id])
  profileUrl String?
  metadata   Json?
}

```


## app/api/src/index.ts
```
import { PrismaClient } from '@prisma/client';
import express, { Request, Response, NextFunction } from 'express';

const prisma = new PrismaClient();
const app = express();
const port = process.env.PORT || 3000;

// prisma helpers
async function checkPrismaConnection() {
    try {
        await prisma.$queryRaw`SELECT 1`
        return { status: 'OK' }
    } catch (error) {
        // @ts-ignore
        return { status: 'ERROR', error: error.message }
    }
}
// Middleware
app.use(express.json());
app.use((req: Request, res: Response, next: NextFunction) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE');
    res.header('Access-Control-Allow-Headers', 'Content-Type');
    next();
});

// api
app.get('/status', async (req: Request, res: Response) => {
    try {
        const prismaStatus = await checkPrismaConnection()
        res.json({
            ...prismaStatus,
            timestamp: new Date().toISOString()
        });
    } catch (error) {
        console.error('Database connection error:', error);
        // Проверяем тип ошибки
        const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
        res.status(500).json({
            status: 'ERROR',
            db: 'disconnected',
            error: errorMessage
        });
    }
});


app.get('/users', async (req: Request, res: Response) => {
    try {
        const users = await prisma.user.findMany({
            include: { data: true },
        });
        res.json(users);
    } catch (error) {
        console.error('Error fetching users:', error);
        const errorMessage = error instanceof Error ? error.message : 'Internal server error';
        res.status(500).json({ error: errorMessage });
    }
});

// main
async function main() {
    try {
        console.log("Connecting to DB with URL:", process.env.DB_URL);
        await prisma.$connect();
        console.log('Successfully connected to database');
        // Слушаем только HTTP; HTTPS обрабатывает Nginx
        app.listen(port, () => {
            console.log(`HTTP server running on port ${port}`);
        });
    } catch (error) {
        console.error('Failed to connect to database:', error instanceof Error ? error.message : error);
        process.exit(1);
    }
}

// run
main()
    .catch((e) => {
        console.error(e);
        process.exit(1);
    })
    .finally(async () => {
        process.on('SIGTERM', async () => {
            console.log('SIGTERM signal received: closing HTTP server');
            await prisma.$disconnect();
            process.exit(0);
        });
    });

```


## app/api/start-dev.sh
```
podman build -f Dockerfile.local -t my-api-dev .
podman run -p 3000:3000 -v $(pwd):/app -v /app/node_modules my-api-dev

```


## app/api/tsconfig.json
```
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "moduleResolution": "node"
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules"]
}

```


## app/docker-compose-dev.yml
```
version: '3.8'

services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: mandala
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d mandala"]
      interval: 5s
      timeout: 5s
      retries: 5

  web:
    build:
      context: ./web
      dockerfile: Dockerfile.local
    ports:
      - "5173:5173"
    volumes:
      - ./web:/app  # Должен быть правильный путь относительно docker-compose.yml
      - /app/node_modules
    environment:
      - VITE_API_URL=http://localhost:3000
    depends_on:
      - api

  api:
    build:
      context: ./api
      dockerfile: Dockerfile.local
    ports:
      - "3000:3000"
    volumes:
      - ./api:/app
      - /app/node_modules
    environment:
      - DB_URL=postgresql://user:password@postgres:5432/mandala
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  postgres_data:

```


## app/start-dev.sh
```
#!/bin/bash

# Останавливаем и удаляем все контейнеры
echo "🛑 Останавливаем и удаляем существующие контейнеры..."
podman-compose -f docker-compose-dev.yml down -v

# Пересобираем и запускаем контейнеры
echo "🔨 Пересобираем и запускаем контейнеры..."
podman-compose -f docker-compose-dev.yml up --build -d

# Ждем пока PostgreSQL будет готов принимать подключения
echo "⏳ Ожидаем готовности PostgreSQL..."
while ! podman-compose -f docker-compose-dev.yml exec postgres pg_isready -U user -d mandala; do
  sleep 2
done

# Применяем миграции Prisma
echo "🔄 Применяем миграции базы данных..."
podman-compose -f docker-compose-dev.yml exec api npx prisma migrate dev --name "dev_migration_$(date +%Y%m%d_%H%M%S)"

# Генерируем Prisma Client (на всякий случай)
echo "⚙️ Генерируем Prisma Client..."
podman-compose -f docker-compose-dev.yml exec api npx prisma generate

# Проверяем состояние миграций
echo "🔍 Проверяем состояние базы данных..."
podman-compose -f docker-compose-dev.yml exec api npx prisma migrate status

# Открываем браузер
echo "🌐 Открываем приложение в браузере..."
sleep 2  # Даем веб-серверу немного времени для запуска
open "http://localhost:5173/"

echo "✅ Готово! Приложение запущено и миграции применены."

```


## app/web/Dockerfile
```
# Стадия сборки приложения
FROM node:20.12.2-alpine AS build

WORKDIR /app
ARG VITE_API_URL
ENV VITE_API_URL=${VITE_API_URL}
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Базовый образ для production
FROM nginx:alpine

# Установка Certbot и зависимостей
RUN apk add --no-cache certbot certbot-nginx bash openssl && \
    mkdir -p /var/www/certbot && \
    rm /etc/nginx/conf.d/default.conf

# Копирование конфигов
COPY ./nginx.conf /etc/nginx/conf.d/
COPY --from=build /app/dist /usr/share/nginx/html

# Скрипт для инициализации и обновления сертификатов
COPY init-letsencrypt.sh /docker-entrypoint.d/

# Права на выполнение скриптов
RUN chmod +x /docker-entrypoint.d/init-letsencrypt.sh

# Открываем порты HTTP и HTTPS
EXPOSE 80
EXPOSE 443

```


## app/web/Dockerfile.local
```
FROM node:20.12.2-alpine

WORKDIR /app

# Копируем зависимости отдельным слоем
COPY package.json package-lock.json* ./

# Установка зависимостей (чистый кэш для уменьшения размера образа)
RUN npm install --silent --no-optional --no-fund

# Копируем исходный код
COPY . .

# Открываем порт разработки Vite
EXPOSE 5173

# Запускаем сервер разработки с открытым хостом
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

```


## app/web/eslint.config.js
```
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
)

```


## app/web/index.html
```
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Mandala MiniApp</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>

```


## app/web/init-letsencrypt.sh
```
#!/bin/bash

# Проверяем наличие сертификатов
if [ ! -d "/etc/letsencrypt/live/mandala-app.online" ]; then
  echo "Initializing Certbot..."
  certbot certonly --non-interactive --agree-tos --standalone \
    -d mandala-app.online \
    -d mandala-app.ru \
    --email sshishkintolik@mail.ru \
    --preferred-challenges http
fi

# Настройка автоматического обновления
echo "0 3 * * * /usr/bin/certbot renew --quiet --post-hook 'nginx -s reload'" >> /etc/crontabs/root

# Запуск cron и Nginx
crond && nginx -g 'daemon off;'

```


## app/web/nginx.conf
```
server {
    listen 80;
    server_name mandala-app.online mandala-app.ru www.*;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name mandala-app.online mandala-app.ru www.*;

    ssl_certificate /etc/letsencrypt/live/mandala-app.online/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mandala-app.online/privkey.pem;

    # Оптимизация SSL
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
}

```


## app/web/package.json
```
{
  "name": "src",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@twa-dev/sdk": "^8.0.2"
  },
  "devDependencies": {
    "@eslint/js": "^9.22.0",
    "@types/react": "^19.0.10",
    "@types/node": "^22.15.18",
    "@types/react-dom": "^19.0.4",
    "@vitejs/plugin-react": "^4.3.4",
    "eslint": "^9.22.0",
    "eslint-plugin-react-hooks": "^5.2.0",
    "eslint-plugin-react-refresh": "^0.4.19",
    "globals": "^16.0.0",
    "typescript": "~5.7.2",
    "typescript-eslint": "^8.26.1",
    "vite": "^6.3.1"
  }
}

```


## app/web/src/App.tsx
```
import { useState, useEffect } from 'react'
import WebApp from '@twa-dev/sdk'

import './App.css'

function App() {
  const [count, setCount] = useState(0)
  const [apiStatus, setApiStatus] = useState<any>(null)

    useEffect(() => {
      WebApp.ready();
  }, []);
  const user = WebApp.initDataUnsafe.user;

    const fetchApiStatus = async () => {
        try {
            const apiUrl = window.location.hostname.includes('mandala-app')
                ? '//api.' + window.location.hostname
                : 'http://localhost:3000';
            const response = await fetch(`${apiUrl}/status`)
            const data = await response.json()
            setApiStatus(data)
        } catch (error) {
            console.error('Ошибка при запросе к API:', error)
            setApiStatus({ error: 'Не удалось получить данные' })
        }
    }

    return (
      <>
          <h2>Моя Мандала2</h2>
          <div className="card">
              <button onClick={() => setCount((count) => count + 1)}>
                  прожито жизней = {count}
              </button>
              <button onClick={fetchApiStatus} style={{marginTop: '10px'}}>
                  Сходить в API
              </button>
          </div>
          {
              user && (
                  <div>
                      <p>Привет, {user.first_name}!</p>
                      <p>ID: {user.id}</p>
                  </div>
              )
          }
          {apiStatus && (
              <div style={{ marginTop: '20px' }}>
                  <h3>Ответ API:</h3>
                  <pre>{JSON.stringify(apiStatus, null, 2)}</pre>
              </div>
          )}
          <button onClick={() => WebApp.close()}>Close App</button>

      </>
  )
}

export default App

```


## app/web/src/main.tsx
```
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

```


## app/web/src/manifest.json
```
{
  "name": "Mandala App",
  "short_name": "Mandala",
  "start_url": "/",
  "display": "standalone",
  "theme_color": "#ffffff",
  "background_color": "#ffffff",
  "icons": [
    {
      "src": "/icon-192x192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/icon-512x512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}

```


## app/web/src/vite-env.d.ts
```
/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_API_URL: string
}

interface ImportMeta {
    readonly env: ImportMetaEnv
}

```


## app/web/start.sh
```
podman build -f Dockerfile.local -t my-app-dev .
podman run -p 5173:5173 -v $(pwd):/app -v /app/node_modules my-app-dev

```


## app/web/tsconfig.app.json
```
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.app.tsbuildinfo",
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,

    /* Bundler mode */
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",

    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["src"]
}

```


## app/web/tsconfig.json
```
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}

```


## app/web/tsconfig.node.json
```
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.node.tsbuildinfo",
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,

    /* Bundler mode */
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,

    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["vite.config.ts"]
}

```


## app/web/vite.config.ts
```
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/',  // Добавьте эту строку
  plugins: [react()],
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(process.env.VITE_API_URL)
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    headers: {
      'Content-Security-Policy': "frame-src 'self' https://telegram.org"
    }
  }
})

```
