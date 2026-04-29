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
