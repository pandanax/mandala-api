output "dns_recordset_id" {
  description = "ID созданной записи DNS."
  value       = yandex_dns_recordset.mandala_http.id
}

output "fqdn_hint" {
  description = "Имя записи относительно зоны (полный FQDN зависит от зоны в консоли)."
  value       = var.dns_record_name
}
