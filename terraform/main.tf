resource "yandex_dns_recordset" "mandala_http" {
  zone_id = var.dns_zone_id
  name    = var.dns_record_name
  type    = "A"
  ttl     = var.dns_ttl
  data    = [var.vm_public_ip]
}
