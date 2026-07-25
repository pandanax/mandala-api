# Mandala: дашборд наблюдаемости в YC Monitoring (дашборд-as-code).
#
# Метрики эмитит само приложение в service=custom (см. src/mandala/metrics.py).
# Имена метрик и метки в запросах ниже ДОЛЖНЫ совпадать с константами в metrics.py:
#   mandala.http.requests    COUNTER {route, method, status}
#   mandala.http.latency_ms  DGAUGE  {route, stat=avg|max}
#   mandala.telegram.delivery COUNTER {method, outcome=ok|error}
#   mandala.llm.requests     COUNTER {outcome=ok|error|timeout}
#   mandala.llm.latency_ms   DGAUGE  {stat=avg|max}
#   mandala.app.up           IGAUGE  (liveness-heartbeat = 1)
#
# Синтаксис query — язык YC Monitoring (НЕ PromQL):
#   * селектор: "<имя.метрики>"{service="custom", label="value"} — имя ПЕРЕД скобками,
#     в ДВОЙНЫХ кавычках; значения меток тоже в двойных (в HCL экранируются как \").
#     Формы {name='...'} с одинарными кавычками / без double-quote — не строятся.
#   * per-second из COUNTER: non_negative_derivative(<селектор>). Функции rate() в YC
#     НЕТ (её использование = «Ошибка построения графика»).
#   * glob в значениях меток: status="4*", route="/webhooks/telegram*".
#   * DGAUGE (латентность avg/max) берётся селектором как есть, без derivative.
#
# Ресурс аддитивный: не трогает VM, Managed PostgreSQL и DNS.

locals {
  logs_url = (
    var.log_group_id != ""
    ? "https://console.yandex.cloud/folders/${var.folder_id}/logging/group/${var.log_group_id}/logs"
    : "https://console.yandex.cloud/folders/${var.folder_id}/logging"
  )

  intro_md = <<-EOT
    ## Mandala — наблюдаемость

    Метрики (`service=custom`) эмитит приложение — см. `src/mandala/metrics.py`
    и `docs/monitoring.md`. Дашборд аддитивен: VM, БД и DNS не затрагивает.

    **Логи:** [YC Logging](${local.logs_url}) — структурные `funnel …` строки из
    stdout контейнера (`src/mandala/observability.py`).
  EOT
}

resource "yandex_monitoring_dashboard" "mandala" {
  name        = var.dashboard_name
  title       = "Mandala — наблюдаемость"
  folder_id   = var.folder_id
  description = "TG / LLM / приложение: health, RPS, латентность, ошибки. Управляется Terraform."

  labels = {
    app       = "mandala"
    managedby = "terraform"
  }

  # --- Шапка: описание + ссылка на логи ---
  widgets {
    position {
      x = 0
      y = 0
      w = 12
      h = 2
    }
    text {
      text = local.intro_md
    }
  }

  # ====================== Приложение ======================
  widgets {
    position {
      x = 0
      y = 2
      w = 12
      h = 1
    }
    title {
      text = "Приложение — health / RPS / латентность / ошибки"
    }
  }

  widgets {
    position {
      x = 0
      y = 3
      w = 6
      h = 4
    }
    chart {
      chart_id = "app-rps"
      title    = "RPS по роутам/методам/статусам"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 6
      y = 3
      w = 6
      h = 4
    }
    chart {
      chart_id = "app-errors"
      title    = "Ошибки HTTP (4xx / 5xx), в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", status=\"4*\"})"
        }
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", status=\"5*\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 0
      y = 7
      w = 6
      h = 4
    }
    chart {
      chart_id = "app-latency"
      title    = "Латентность ответа, мс (avg / max)"
      queries {
        target {
          query = "\"mandala.http.latency_ms\"{service=\"custom\", stat=\"avg\"}"
        }
        target {
          query = "\"mandala.http.latency_ms\"{service=\"custom\", stat=\"max\"}"
        }
      }
    }
  }

  widgets {
    position {
      x = 6
      y = 7
      w = 6
      h = 4
    }
    chart {
      chart_id = "app-health"
      title    = "Health — доступность (up=1) и /health, в секунду"
      queries {
        target {
          query = "\"mandala.app.up\"{service=\"custom\"}"
        }
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", route=\"/health\"})"
        }
      }
    }
  }

  # ====================== Telegram ======================
  widgets {
    position {
      x = 0
      y = 11
      w = 12
      h = 1
    }
    title {
      text = "Telegram — вебхуки и доставка Bot API"
    }
  }

  widgets {
    position {
      x = 0
      y = 12
      w = 6
      h = 4
    }
    chart {
      chart_id = "tg-updates"
      title    = "Апдейты/вебхуки, в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", route=\"/webhooks/telegram*\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 6
      y = 12
      w = 6
      h = 4
    }
    chart {
      chart_id = "tg-webhook-errors"
      title    = "Ошибки вебхука (4xx / 5xx), в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", route=\"/webhooks/telegram*\", status=\"4*\"})"
        }
        target {
          query = "non_negative_derivative(\"mandala.http.requests\"{service=\"custom\", route=\"/webhooks/telegram*\", status=\"5*\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 0
      y = 16
      w = 6
      h = 4
    }
    chart {
      chart_id = "tg-latency"
      title    = "Латентность обработки webhook, мс (avg / max)"
      queries {
        target {
          query = "\"mandala.http.latency_ms\"{service=\"custom\", route=\"/webhooks/telegram*\", stat=\"avg\"}"
        }
        target {
          query = "\"mandala.http.latency_ms\"{service=\"custom\", route=\"/webhooks/telegram*\", stat=\"max\"}"
        }
      }
    }
  }

  widgets {
    position {
      x = 6
      y = 16
      w = 6
      h = 4
    }
    chart {
      chart_id = "tg-delivery"
      title    = "Доставка Bot API по методу и исходу, в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.telegram.delivery\"{service=\"custom\"})"
        }
      }
    }
  }

  # ====================== LLM ======================
  widgets {
    position {
      x = 0
      y = 20
      w = 12
      h = 1
    }
    title {
      text = "LLM — запросы, латентность, ошибки/timeout"
    }
  }

  widgets {
    position {
      x = 0
      y = 21
      w = 6
      h = 4
    }
    chart {
      chart_id = "llm-rps"
      title    = "Запросы LLM по исходу, в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.llm.requests\"{service=\"custom\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 6
      y = 21
      w = 6
      h = 4
    }
    chart {
      chart_id = "llm-errors"
      title    = "Ошибки и timeout'ы LLM, в секунду"
      queries {
        target {
          query = "non_negative_derivative(\"mandala.llm.requests\"{service=\"custom\", outcome=\"error\"})"
        }
        target {
          query = "non_negative_derivative(\"mandala.llm.requests\"{service=\"custom\", outcome=\"timeout\"})"
        }
      }
    }
  }

  widgets {
    position {
      x = 0
      y = 25
      w = 12
      h = 4
    }
    chart {
      chart_id = "llm-latency"
      title    = "Латентность LLM, мс (avg / max)"
      queries {
        target {
          query = "\"mandala.llm.latency_ms\"{service=\"custom\", stat=\"avg\"}"
        }
        target {
          query = "\"mandala.llm.latency_ms\"{service=\"custom\", stat=\"max\"}"
        }
      }
    }
  }
}
