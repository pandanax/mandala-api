variable "folder_id" {
  type        = string
  description = "ID каталога Yandex Cloud (yc config get folder-id)."
}

variable "default_zone" {
  type        = string
  description = "Зона провайдера по умолчанию (например ru-central1-b)."
  default     = "ru-central1-b"
}

variable "dns_zone_id" {
  type        = string
  description = "ID публичной DNS-зоны (yc dns zone list --format json)."
}

variable "dns_record_name" {
  type        = string
  description = "Относительное имя записи внутри зоны (например api → api.<зона>)."
  default     = "api"
}

variable "dns_ttl" {
  type        = number
  description = "TTL записи A, секунды."
  default     = 600
}

variable "vm_public_ip" {
  type        = string
  description = "Публичный IPv4 ВМ с Nginx (например yc compute instance get --name n8n-server)."
}

variable "dashboard_name" {
  type        = string
  description = "Имя дашборда YC Monitoring (уникально в каталоге)."
  default     = "mandala-observability"
}

variable "log_group_id" {
  type        = string
  description = "ID log-группы YC Logging для ссылки на логи (yc logging group list). Пусто → ссылка на список групп. После создания группы (logging.tf) можно подставить сюда её id из `terraform output logging_group_id`."
  default     = ""
}

variable "log_group_name" {
  type        = string
  description = "Имя log-группы YC Logging для логов приложения (уникально в каталоге). См. logging.tf."
  default     = "mandala-logs"
}

variable "log_group_retention_period" {
  type        = string
  description = "Срок хранения записей в log-группе (Go-duration: s/m/h; кратно часам, максимум по лимиту YC). По умолчанию 3 суток."
  default     = "72h"
}
