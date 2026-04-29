# Исходный код проекта (./app/api)

Это полный исходный код проекта, внимательно изучите структуру проекта и содержимое файлов.

## Структура проекта
```
├── Dockerfile
├── Dockerfile.local
├── entrypoint.sh
├── nginx-api.conf
├── nginx.conf
├── package.json
├── prisma
│   ├── prisma/migrations
│   │   ├── prisma/migrations/20250603190457_pervaya_migracziya_posle_dobavleniya
│   │   │   └── prisma/migrations/20250603190457_pervaya_migracziya_posle_dobavleniya/migration.sql
│   │   └── prisma/migrations/migration_lock.toml
│   └── prisma/schema.prisma
├── src
│   └── src/index.ts
├── start-dev.sh
└── tsconfig.json
```
## Содержимое файлов


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

RUN apk add --no-cache nginx postgresql-client bash openssl certbot
RUN mkdir -p /var/www/certbot /etc/letsencrypt/live/api.mandala-app.online/
RUN openssl req -x509 -nodes -days 3 -newkey rsa:2048 \
  -subj "/C=RU/ST=Moscow/L=Moscow/O=Test/CN=api.mandala-app.online" \
  -keyout /etc/letsencrypt/live/api.mandala-app.online/privkey.pem \
  -out /etc/letsencrypt/live/api.mandala-app.online/fullchain.pem

WORKDIR /app

# Копируем билд и модули из билдера
COPY --from=api-builder /app/package.json /app/package-lock.json ./
COPY --from=api-builder /app/node_modules ./node_modules
COPY --from=api-builder /app/dist ./dist
COPY --from=api-builder /app/prisma ./prisma

RUN npm ci --only=production --no-optional

# Nginx config и энтрипойнт
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx-api.conf /etc/nginx/conf.d/api.conf
COPY nginx.conf /etc/nginx/nginx.conf

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


## app/api/nginx.conf
```
user  nginx;
worker_processes  auto;

error_log  /var/log/nginx/error.log warn;
pid        /run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile        on;
    keepalive_timeout  65;

    include /etc/nginx/conf.d/*.conf;
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
    "@prisma/client": "^6.9.0",
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
