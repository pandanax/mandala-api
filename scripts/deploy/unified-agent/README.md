# Доставка логов Mandala → YC Logging (Unified Agent на ВМ)

Аддитивная доставка структурных логов приложения (`funnel …` из stdout контейнера
`mandala-http`) в **YC Logging**. Полный гайд — **[docs/logging.md](../../../docs/logging.md)**.

Схема: `docker logs -f mandala-http` → `/var/log/mandala/app.log` → **Unified Agent**
(`file_input` → `yc_logs`) → log-группа YC Logging. Путь деплоя (`restart_app.sh`/
`deploy.sh`), контейнер бота, nginx и n8n **не** затрагиваются; `docker logs` продолжает
работать; в группу идут логи только `mandala-http`.

| Файл | Роль |
|------|------|
| `config.yml` | Конфиг Unified Agent (`file_input` → `yc_logs`, IAM через metadata SA ВМ). |
| `mandala-logship.service` | systemd: `docker logs -f --tail 0 mandala-http >> /var/log/mandala/app.log`. |
| `mandala-unified-agent.service` | systemd: контейнер Unified Agent с монтированием конфига/файла/буфера. |
| `logrotate-mandala` | Ротация `app.log` (`copytruncate`). |
| `install.sh` | Идемпотентный установщик всего перечисленного на ВМ. |

## Быстрый старт (на ВМ)

Предпосылки: создана log-группа (`terraform apply` в [`terraform/`](../../../terraform/) →
`terraform output logging_group_id`) и у сервисного аккаунта ВМ есть роль
`logging.writer`.

```bash
scp -r scripts/deploy/unified-agent ubuntu@api.mandala-app.online:/tmp/
ssh ubuntu@api.mandala-app.online \
  'sudo LOG_GROUP_ID=<logging_group_id> bash /tmp/unified-agent/install.sh'
```

Проверка и разбор ошибок — в [docs/logging.md](../../../docs/logging.md).
