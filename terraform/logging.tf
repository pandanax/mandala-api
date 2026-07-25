# Mandala: log-группа YC Logging для структурных логов приложения (дашборд-as-code).
#
# Приложение пишет `funnel …` строки в stdout контейнера (src/mandala/observability.py).
# На проде (ВМ + docker) stdout сам по себе в YC Logging НЕ попадает — доставку делает
# Unified Agent на ВМ (см. scripts/deploy/unified-agent/ и docs/logging.md): он читает
# stdout контейнера из файла и пушит записи в ЭТУ log-группу через её ID.
#
# Ресурс АДДИТИВНЫЙ и недеструктивный: не трогает VM, Managed PostgreSQL, DNS
# (main.tf) и дашборд (monitoring.tf). Инкрементальный `terraform apply` при уже
# накатанном состоянии = `Plan: 1 to add` (или таргетно:
#   terraform apply -target=yandex_logging_group.mandala).
#
# ID созданной группы → `terraform output logging_group_id`; его нужно:
#   1) передать Unified Agent как env LOG_GROUP_ID (доставка логов);
#   2) опционально положить в terraform.tfvars как log_group_id — тогда шапка дашборда
#      (monitoring.tf) даст прямую ссылку на эту группу (иначе — ссылка на список групп).

resource "yandex_logging_group" "mandala" {
  name             = var.log_group_name
  folder_id        = var.folder_id
  retention_period = var.log_group_retention_period
  description      = "Структурные логи приложения Mandala (funnel …) с ВМ через Unified Agent. Управляется Terraform."

  labels = {
    app       = "mandala"
    managedby = "terraform"
  }
}
