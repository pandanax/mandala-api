# Mandala: аддитивная DNS-запись под целевую схему MVP (тикет 23).
# Не трогает VM, кластер PostgreSQL и существующий n8n — только A-запись в уже существующей зоне.
# Перед apply: скопировать terraform.tfvars.example → terraform.tfvars (не в git).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = ">= 0.130.0"
    }
  }
}

provider "yandex" {
  folder_id = var.folder_id
  zone      = var.default_zone
}
