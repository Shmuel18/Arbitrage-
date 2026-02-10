# 🐳 Docker Quick Start

## Prerequisites

- Docker Desktop installed
- Docker Compose v2+

## Start Infrastructure

### All Services (Database + Redis)

```powershell
docker-compose up -d
```

### With Monitoring (adds Prometheus + Grafana)

```powershell
docker-compose --profile monitoring up -d
```

## Check Status

```powershell
docker-compose ps
```

## View Logs

```powershell
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f postgres
docker-compose logs -f redis
```

## Connect to Services

### PostgreSQL

```powershell
# From host
psql -h localhost -U trinity -d trinity_arbitrage

# From Docker
docker-compose exec postgres psql -U trinity -d trinity_arbitrage
```

### Redis

```powershell
# From host
redis-cli

# From Docker
docker-compose exec redis redis-cli
```

### Prometheus

- URL: http://localhost:9090

### Grafana

- URL: http://localhost:3000
- Username: admin
- Password: (set in docker-compose.yml)

## Stop Services

```powershell
# Stop but keep data
docker-compose stop

# Stop and remove containers (keeps volumes)
docker-compose down

# Stop and remove everything including data
docker-compose down -v
```

## Backup Data

```powershell
# Backup PostgreSQL
docker-compose exec postgres pg_dump -U trinity trinity_arbitrage > backup.sql

# Backup Redis
docker-compose exec redis redis-cli BGSAVE
```

## Production Notes

⚠️ **Before Production:**

1. Change default passwords in `docker-compose.yml`
2. Use Docker secrets for sensitive data
3. Configure volume backups
4. Set proper resource limits
5. Enable TLS/SSL
6. Configure firewall rules
