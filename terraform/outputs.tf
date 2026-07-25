output "dns_recordset_id" {
  description = "ID созданной записи DNS."
  value       = yandex_dns_recordset.mandala_http.id
}

output "fqdn_hint" {
  description = "Имя записи относительно зоны (полный FQDN зависит от зоны в консоли)."
  value       = var.dns_record_name
}

output "dashboard_id" {
  description = "ID дашборда YC Monitoring."
  value       = yandex_monitoring_dashboard.mandala.dashboard_id
}

output "dashboard_url" {
  description = "Ссылка на дашборд в консоли YC."
  value       = "https://console.yandex.cloud/folders/${var.folder_id}/monitoring/dashboards/${yandex_monitoring_dashboard.mandala.dashboard_id}"
}
